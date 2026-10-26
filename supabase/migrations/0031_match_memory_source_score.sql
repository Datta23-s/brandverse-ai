-- 0031: app.match_memory returns the item's source score, so E can enter the M-score
-- (Memory.md section 3.1: "the scorer, then the migration, then app.match_memory returning the
-- stored score, then the caller in Phase 6").
--
-- 0028 stores the score: memory_item_sources holds one row per (item, source) and
-- memory_items.source_score is the derived maximum, null for an item with no sources. The scorer
-- (backend/app/research/source_scoring.py) computes it and the Phase 6 caller will write it. The
-- retrieval function did not return it, so `Candidate.source_score` was always None in the
-- application and E was left out of every M-score whatever the database held.
--
-- E = source_score x confidence, and a missing E is LEFT OUT and the remaining weights
-- renormalised (backend/app/memory/scoring.py), never filled with a default. So an item with no
-- sources still scores, on the components it has; nothing here invents a value for it.
--
-- The function's returned columns change, which CREATE OR REPLACE cannot do, so it is dropped and
-- created. Everything else is 0022 unchanged: SECURITY INVOKER (the caller's row-level security
-- applies on top of the explicit org filter), search_path pinned, seed memory excluded unless
-- asked for, RRF with k = 60, hnsw.ef_search left to the caller. The grants are restored below
-- because DROP takes them with it; `anon` holds none, as in 0022.
--
-- source_score sits beside confidence, the other trust-deciding column it multiplies, not at the
-- end of the row: the tests and the application read these columns by name.
--
-- Down: the 0022 body as it stands (git show 771eba0:supabase/migrations/0022_match_memory.sql), i.e.
--   drop function app.match_memory(uuid, public.vector, text, uuid, boolean, integer);
--   then create it without the source_score column, and restore the same grants.
drop function app.match_memory(uuid, public.vector, text, uuid, boolean, integer);

create function app.match_memory(
  p_org_id       uuid,
  p_query_embed  public.vector(768),
  p_query_text   text,
  p_brand_id     uuid    default null,
  p_include_seed boolean default false,
  p_limit        integer default 20
) returns table (
  id uuid, content text, kind text, brand_id uuid, product_id uuid, product_category text,
  claim_id uuid, claim_type text, sector text, source_url text, provenance text,
  confidence numeric, source_score numeric, created_at timestamptz, freshness_days integer,
  similarity double precision, vec_rank integer, txt_rank integer, rrf numeric
)
language sql stable security invoker
set search_path = ''
as $$
with scoped as (
  select m.*
  from public.memory_items m
  where m.org_id = p_org_id
    and not m.revoked
    and m.approved
    and (p_include_seed or not m.is_seed)
    and (p_brand_id is null or m.brand_id = p_brand_id or m.brand_id is null)
),
vec as (
  select s.id, row_number() over (order by s.embedding OPERATOR(public.<=>) p_query_embed) as r
  from scoped s
  where s.embedding is not null
  order by s.embedding OPERATOR(public.<=>) p_query_embed
  limit 60
),
txt as (
  select ranked.id, ranked.r
  from (
    select s.id, row_number() over (
             order by pg_catalog.ts_rank_cd(pg_catalog.to_tsvector('english', s.content),
                                            pg_catalog.websearch_to_tsquery('english', p_query_text))
                      desc, s.id) as r
    from scoped s
    where pg_catalog.to_tsvector('english', s.content)
          @@ pg_catalog.websearch_to_tsquery('english', p_query_text)
  ) ranked
  order by ranked.r
  limit 60
)
select s.id, s.content, s.kind, s.brand_id, s.product_id, p.category, s.claim_id, c.claim_type,
       s.sector, s.source_url, s.provenance, s.confidence, s.source_score, s.created_at,
       s.freshness_days,
       case when s.embedding is null then null
            else 1 - (s.embedding OPERATOR(public.<=>) p_query_embed) end as similarity,
       v.r::integer, t.r::integer,
       (coalesce(1.0 / (60 + v.r), 0) + coalesce(1.0 / (60 + t.r), 0))::numeric as rrf
from scoped s
left join vec v on v.id = s.id
left join txt t on t.id = s.id
left join public.products p on p.id = s.product_id and p.org_id = s.org_id
left join public.product_claims c on c.id = s.claim_id and c.org_id = s.org_id
where v.id is not null or t.id is not null
order by rrf desc, s.id
limit p_limit
$$;

revoke all on function app.match_memory(uuid, public.vector, text, uuid, boolean, integer)
  from public, anon;
grant execute on function app.match_memory(uuid, public.vector, text, uuid, boolean, integer)
  to authenticated, service_role;
