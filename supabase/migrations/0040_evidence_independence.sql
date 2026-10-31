-- 0040: independence is kept with a memory item's evidence (Phase 6 chunk D, threat V6; owner 2026-10-31).
--
-- 0039 lets the system say that a page is the brand's own. Without a place to keep that fact beside the
-- evidence, a memory item reused in a later run would lose the provenance that made its evidence
-- first-party, and V6 would be closed on the findings path and open on the memory path: a half-fix that
-- reads as fixed. So the fact is kept per source and derived per item.
--
--   memory_item_sources.first_party    true: the source is the brand's own page; false: it is not;
--                                      NULL: unknown (no brand domain was registered when it was
--                                      written). Written by the backend with the source row, which is
--                                      append-only, so it is never revised: it is what was known
--                                      at the time. It is a fact BESIDE the source's score, not a term
--                                      in it: authority and independence stay separate.
--   memory_items.evidence_first_party_only
--                                      derived by trigger from the item's sources, like source_score
--                                      (0028) and guarded the same way: TRUE when it has sources and
--                                      every one is first-party; FALSE when at least one source is not
--                                      the brand's own (independent evidence exists); NULL otherwise
--                                      (no sources, or some of unknown status and none independent).
--                                      Unknown is never read as independent.
--
-- app.match_memory returns the derived flag, so a recalled item carries it into the run. It is not an
-- input to the M-score (app/memory/scoring.py): independence decides what a claim may rest on (the
-- claim gate's sole-evidence rule), not how relevant a memory is.
--
-- Down: restore app.match_memory from 0031; drop trigger memory_items_evidence_derived on memory_items;
--   drop trigger memory_item_sources_first_party on memory_item_sources; drop both functions;
--   alter table memory_items drop column evidence_first_party_only;
--   alter table memory_item_sources drop column first_party;

alter table memory_item_sources add column first_party boolean;
alter table memory_items add column evidence_first_party_only boolean;

create function app.memory_item_sources_first_party() returns trigger
language plpgsql set search_path = '' as $$
begin
  update public.memory_items m
     set evidence_first_party_only = (
       select case when pg_catalog.bool_or(s.first_party is false) then false
                   when pg_catalog.bool_and(s.first_party is true) then true
                   else null end
         from public.memory_item_sources s
        where s.memory_item_id = new.memory_item_id and s.org_id = new.org_id)
   where m.id = new.memory_item_id and m.org_id = new.org_id;
  return null;
end $$;
create trigger memory_item_sources_first_party after insert on memory_item_sources
  for each row execute function app.memory_item_sources_first_party();

create function app.memory_items_evidence_derived() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    if new.evidence_first_party_only is not null then
      raise exception 'memory_items.evidence_first_party_only is derived from memory_item_sources';
    end if;
  elsif new.evidence_first_party_only is distinct from old.evidence_first_party_only
        and pg_catalog.pg_trigger_depth() < 2 then
    raise exception 'memory_items.evidence_first_party_only is derived from memory_item_sources';
  end if;
  return new;
end $$;
create trigger memory_items_evidence_derived before insert or update on memory_items
  for each row execute function app.memory_items_evidence_derived();

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
  confidence numeric, source_score numeric, evidence_first_party_only boolean,
  created_at timestamptz, freshness_days integer,
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
       s.sector, s.source_url, s.provenance, s.confidence, s.source_score,
       s.evidence_first_party_only, s.created_at,
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
