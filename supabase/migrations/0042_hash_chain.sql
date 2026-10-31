-- 0042: hash chain on approvals and audit_log (Architecture.md section 9, hardening decision 2;
-- moved into Phase 6 chunk E by the owner, 2026-10-31).
--
-- Each new row stores `prev_hash` (the previous row's `row_hash`, or 'genesis') and
-- `row_hash = sha256(prev_hash | chain_seq | the row as canonical jsonb)`. Inserts are serialised by
-- an advisory lock and numbered inside the trigger, after the lock is held, so the chain order is
-- the commit order. Verification recomputes every hash and every link.
--
-- DETECTION, NOT PREVENTION, and claimed as nothing more. What it detects: an edited row, a deleted
-- or inserted row anywhere but the end, a swapped order. What it does NOT: the table owner
-- (postgres) can disable the trigger and rewrite the whole chain consistently; deleting the LAST
-- rows leaves a valid shorter chain. The defence for both is the head hash anchored outside the
-- database (committed to git with the weekly pg_dump: scripts/chain_head.py).
--
-- Rows written before this migration are NOT chained (chain_seq null): backfilling them would mean
-- rewriting append-only tables. They are counted by app.chain_verify as `rows_unchained`; the chain
-- covers everything from the first row inserted after 0042.
--
-- Timestamps and numbers are rendered in UTC inside the function (SET TimeZone), so the hash of a
-- row does not depend on the session that inserts or verifies it. Stored generated columns
-- (approvals.approver_role) are not yet computed when a BEFORE trigger runs, so they are left out
-- of the hashed text on both sides; they are a constant of the table.
-- Down (the audit trail is evidence; dropping the chain is a deliberate act, not a rollback):
--   drop trigger z_chain_stamp on approvals; drop trigger z_chain_stamp on audit_log;
--   drop function app.chain_stamp(); drop function app.chain_verify(text);
--   alter table approvals drop column chain_seq, drop column prev_hash, drop column row_hash;
--   alter table audit_log drop column chain_seq, drop column prev_hash, drop column row_hash;
alter table approvals add column chain_seq bigint, add column prev_hash text,
  add column row_hash text;
alter table audit_log add column chain_seq bigint, add column prev_hash text,
  add column row_hash text;
create unique index approvals_chain_seq_key on approvals (chain_seq) where chain_seq is not null;
create unique index audit_log_chain_seq_key on audit_log (chain_seq) where chain_seq is not null;

create function app.chain_stamp() returns trigger
language plpgsql security definer set search_path = '' set timezone = 'UTC' as $$
declare
  last_seq bigint;
  last_hash text;
begin
  perform pg_advisory_xact_lock(hashtextextended('chain:' || tg_table_name, 0));
  execute format(
    'select chain_seq, row_hash from public.%I where chain_seq is not null '
    'order by chain_seq desc limit 1', tg_table_name)
    into last_seq, last_hash;
  new.chain_seq := coalesce(last_seq, 0) + 1;
  new.prev_hash := coalesce(last_hash, 'genesis');
  new.row_hash := encode(sha256(convert_to(
    new.prev_hash || '|' || new.chain_seq::text || '|' ||
    (to_jsonb(new) - 'row_hash' - 'prev_hash' - 'chain_seq' - 'approver_role')::text,
    'UTF8')), 'hex');
  return new;
end $$;

-- Named to sort after every other BEFORE INSERT trigger on the table, so the hash covers the row
-- as it will be stored.
create trigger z_chain_stamp before insert on approvals
  for each row execute function app.chain_stamp();
create trigger z_chain_stamp before insert on audit_log
  for each row execute function app.chain_stamp();

create function app.chain_verify(p_table text)
returns table (rows_chained bigint, rows_unchained bigint, head_seq bigint, head_hash text,
               first_bad_seq bigint, ok boolean)
language plpgsql stable security definer set search_path = '' set timezone = 'UTC' as $$
begin
  if p_table not in ('approvals', 'audit_log') then
    raise exception 'chain_verify: % has no hash chain', p_table;
  end if;
  return query execute format($q$
    with c as (
      select t.chain_seq as seq, t.prev_hash as prev, t.row_hash as hash,
             lag(t.row_hash) over (order by t.chain_seq) as prev_row_hash,
             lag(t.chain_seq) over (order by t.chain_seq) as prev_seq,
             encode(sha256(convert_to(
               t.prev_hash || '|' || t.chain_seq::text || '|' ||
               (to_jsonb(t) - 'row_hash' - 'prev_hash' - 'chain_seq' - 'approver_role')::text,
               'UTF8')), 'hex') as recomputed
      from public.%1$I t where t.chain_seq is not null),
    bad as (
      select min(seq) as first_bad from c
      where recomputed is distinct from hash
         or prev is distinct from coalesce(prev_row_hash, 'genesis')
         or seq <> coalesce(prev_seq, 0) + 1)
    select (select count(*) from c),
           (select count(*) from public.%1$I where chain_seq is null),
           (select max(seq) from c),
           (select hash from c order by seq desc limit 1),
           (select first_bad from bad),
           (select first_bad from bad) is null$q$, p_table);
end $$;

-- Verification is the backend's and the operator's; the API roles have no use for it.
revoke execute on function app.chain_verify(text) from public, anon, authenticated;
grant execute on function app.chain_verify(text) to service_role;
