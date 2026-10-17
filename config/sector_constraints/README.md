# Sector constraint registry — rule files

Cited registry rules (`Architecture.md` §8.1) are written here, one YAML file per sector, reviewed
and committed like code, and applied by `backend/scripts/apply_sector_constraints.py`.

**There are no rule files yet.** The one regulated sector, `health-supplements`, runs against four
synthetic rules that migration 0021 seeded ("Synthetic rule, not derived from any regulation.
Illustrative only."); every sector is `rules_provenance = 'illustrative'`. Substituting a real,
cited rule set is a deployment step, outside the research contribution.

## Format

```yaml
sector: health-supplements        # a key of the sectors table
jurisdiction: IN                  # two capital letters; default IN
rules:
  - kind: prohibited_phrase       # or required_disclaimer
    match_type: phrase            # or regex (Python syntax; must compile)
    pattern: "<wording that must not appear>"
    source_url: https://<the regulation or guidance this rule comes from>
    source_note: "<clause or section>"
    effective_from: 2026-11-01
  - kind: required_disclaimer
    match_type: phrase
    pattern: null                 # null: required on all output; else the trigger
    disclaimer_text: "<the exact text; compared verbatim (sector_registry.verbatim_form)>"
    source_url: https://...
    effective_from: 2026-11-01
    retired_at: null              # set a date to retire the rule; never delete an entry
```

## Rules of the path

- Every entry cites its source with an http(s) `source_url`. Synthetic entries are refused.
- A rule is never edited: change it by retiring the old entry (`retired_at`) and adding a new one.
  An active rule missing from the file stops the run.
- Only committed files are applied; the commit is recorded in `audit_log` with every change.
- The author is a person with an account (`--author-email`); the script writes `created_by`.
- Patterns reach the database as query parameters only (`Rules.md` §3.3).

## Procedure

```
# from backend/: a dry run prints the changes and writes nothing
uv run python -m scripts.apply_sector_constraints ../config/sector_constraints/<sector>.yaml --author-email <you>
# then, after review
uv run python -m scripts.apply_sector_constraints ../config/sector_constraints/<sector>.yaml --author-email <you> --apply
```

When a sector's synthetic rules are all retired and its cited rules are applied, mark it
`authoritative` with the backend role (`update sectors set rules_provenance = 'authoritative'
where id = '<sector>'`); the database refuses this while any synthetic rule is still active.
