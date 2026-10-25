-- 0027: the decay record (0024) carries no negative confidence.
--
-- 0024's pattern allowed a leading minus on both confidences because the decrement could compute
-- one. Write-back now clamps at 0 and 0026 makes a negative confidence unstorable, so a record
-- naming one can only be malformed; the pattern stops accepting it. No other part of the
-- constraint changes. The project held no memory_items rows when this was written.
--
-- Down: restore memory_items_revocation_recorded from 0024.
alter table memory_items
  drop constraint memory_items_revocation_recorded,
  add constraint memory_items_revocation_recorded check (
    not revoked
    or (
      revoked_at is not null
      and (revoked_by is null) = coalesce(
        revoked_reason ~ ('^confidence_decay: confidence [0-9]\.[0-9]{2} -> [0-9]\.[0-9]{2} '
                          'below [0-9]\.[0-9]{2}; '
                          'cause (validation_flag:[a-z_]+|reviewer_edit:[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}); '
                          'run [0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
        false)
      and (
        revoked_by is not null
        or (
          pg_catalog.substring(revoked_reason, ' -> ([0-9]\.[0-9]{2}) below ')::numeric
            = confidence
          and pg_catalog.substring(revoked_reason, ' -> ([0-9]\.[0-9]{2}) below ')::numeric
            < pg_catalog.substring(revoked_reason, ' below ([0-9]\.[0-9]{2});')::numeric
        )
      )
    )
  );
