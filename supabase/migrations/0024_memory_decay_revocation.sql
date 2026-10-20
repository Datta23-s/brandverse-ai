-- 0024: a memory item revoked by rule — confidence decay (Memory.md §6.3) — is recorded as the
-- system's act, not attributed to a person, and its reason says why that item died.
--
-- Until now a revocation required revoked_by (0006), and 0023 left the backend to "name the
-- revoker" for Phase 4's decay. When a validation-engine flag lowers an item below the revocation
-- threshold, no person acted, so any name would be made up. Decision 2026-10-19 (project owner):
-- a revocation by rule has revoked_by null, and its revoked_reason is a constrained record:
--
--   confidence_decay: confidence <before> -> <after> below <threshold>; cause <cause>; run <uuid>
--
-- where <cause> is validation_flag:<finding kind> or reviewer_edit:<reviewer's user id>, and
-- <after> must equal the row's confidence (so the record and the column cannot disagree, and the
-- confidence of a decayed item is frozen with its revocation). Exactly one of the two kinds holds:
-- revoked_by is null if and only if the reason is a decay record. So "died by decay" against
-- "revoked by a person" is a property of the row, readable by the evaluation (evaluation-plan.md,
-- memory revocations by kind), never a guess from free text.
--
-- A signed-in user cannot produce a decay record: 0023's trigger already sets revoked_by =
-- auth.uid() for them, and now also refuses a reason in the decay format, so a person cannot
-- dress a revocation up as the system's. The project held no memory_items rows when this was
-- written.
--
-- Down: alter table memory_items drop constraint memory_items_revocation_recorded,
--   add constraint memory_items_check1
--     check (not revoked or (revoked_by is not null and revoked_at is not null));
--   restore app.memory_items_revocation() from 0023.
alter table memory_items
  drop constraint memory_items_check1,
  add constraint memory_items_revocation_recorded check (
    not revoked
    or (
      revoked_at is not null
      and (revoked_by is null) = coalesce(
        revoked_reason ~ ('^confidence_decay: confidence -?[0-9]\.[0-9]{2} -> -?[0-9]\.[0-9]{2} '
                          'below [0-9]\.[0-9]{2}; '
                          'cause (validation_flag:[a-z_]+|reviewer_edit:[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}); '
                          'run [0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
        false)
      and (
        revoked_by is not null
        or (
          pg_catalog.substring(revoked_reason, ' -> (-?[0-9]\.[0-9]{2}) below ')::numeric
            = confidence
          and pg_catalog.substring(revoked_reason, ' -> (-?[0-9]\.[0-9]{2}) below ')::numeric
            < pg_catalog.substring(revoked_reason, ' below ([0-9]\.[0-9]{2});')::numeric
        )
      )
    )
  );

create or replace function app.memory_items_revocation() returns trigger
language plpgsql set search_path = '' as $$
begin
  if old.revoked and not new.revoked then
    raise exception 'memory_items: revocation is permanent (Memory.md §6.4)';
  end if;
  if old.revoked then
    if (new.revoked_by, new.revoked_at, new.revoked_reason)
       is distinct from (old.revoked_by, old.revoked_at, old.revoked_reason) then
      raise exception 'memory_items: a revocation is recorded once and never rewritten';
    end if;
    return new;
  end if;
  if new.revoked then
    new.revoked_at := pg_catalog.now();
    if current_user in ('authenticated', 'anon') then
      if new.revoked_reason like 'confidence\_decay:%' then
        raise exception 'memory_items: a decay revocation is recorded by the system, not a person';
      end if;
      new.revoked_by := (select auth.uid());
    end if;
  end if;
  return new;
end $$;
