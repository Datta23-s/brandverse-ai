-- 0022: app.match_memory, hybrid retrieval (Memory.md §4.2): a vector branch and a full-text branch
-- over the caller's organisation, fused by Reciprocal Rank Fusion (k = 60). It produces the
-- CANDIDATE set; the M-score is computed in the application over these candidates, because P, C
-- and A need request context the database does not hold (backend/app/memory/scoring.py).
--
-- As specified, with these changes, each deliberate:
-- 1. SECURITY INVOKER and search_path pinned: the caller's row-level security applies (memory_read,
--    0006), on top of the explicit org filter (Rules.md P3), so an org the caller is not a member
--    of returns nothing whatever p_org_id says. pgvector lives in schema public, so the operator is
--    written OPERATOR(public.<=>).
-- 2. Seed memory is EXCLUDED unless the caller passes p_include_seed (Memory.md §7.1: seeded items
--    never reach evaluation; opt-in, so forgetting the flag excludes them rather than including).
-- 3. The full-text branch orders before its limit (the specification's LIMIT 60 had no ORDER BY,
--    so which 60 matches survived was arbitrary).
-- 4. p_sector is not a parameter: it was declared and unused; sector alignment is the M-score's A.
-- 5. Returns what the M-score needs beside the ranks: cosine similarity (S), the item's product
--    category (P), the backing claim's type (the freshness exemption for verified claims), sector,
--    confidence and freshness.
-- 6. hnsw.ef_search is NOT set here. It is a search-time depth parameter that evaluation sweeps
--    and Phase 6 measures latency against, so the caller sets it per transaction from
--    config/scoring.yaml and logs the value used (backend/app/memory/retrieval.py). search_path
--    stays on the function: that one is a security property, not a tuning knob.
-- Down: drop function app.match_memory(uuid, public.vector, text, uuid, boolean, integer);
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
  confidence numeric, created_at timestamptz, freshness_days integer, similarity double precision,
  vec_rank integer, txt_rank integer, rrf numeric
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
       s.sector, s.source_url, s.provenance, s.confidence, s.created_at, s.freshness_days,
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
