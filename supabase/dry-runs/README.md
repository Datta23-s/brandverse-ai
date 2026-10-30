# Dry runs

The raw output of each rolled-back live dry run (`backend/scripts/live_migration_dry_run.py`), one file per
migration, written by the tool itself with the migration's sha256, the commit and the target host. A later
run of the same migration is compared with its file; a difference exits 3 and leaves the file alone until
`--accept-difference`. `Rules.md` section 3.3.

0032 to 0036 were dry-run and applied on 2026-10-30, before this record existed; their raw output was not
kept, and the description of each is its row in `Architecture.md` section 9.
