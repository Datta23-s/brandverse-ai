# Search-rate measurements — what these files are

Raw logs from probes of how many searches the local SearXNG sustains (`docs/searxng.md`,
`docs/evaluation-plan.md` section 8). One JSON object per line. **Nothing here is a rate that may be
quoted.** The first two files are interrupted runs kept as evidence of what was tried and what
happened, on the same principle as the superseded corpus and the sector-audit rows: a failed attempt
is recorded, not tidied away.

Each search line holds the query, the provider's outcome, result count, rows and failure status per
engine with the reason, and every result **URL**. No titles or snippets are stored (third-party
content is not committed; the snapshot ruling, `docs/evaluation-plan.md` section 8).

## `probe-20261026T151600Z.jsonl` — 4 searches, stopped by hand

The first fixed-interval probe (one search every 10 minutes). It logged counts only, not result URLs,
so the PDF share could not be computed from it; it was stopped by hand at 15:55 UTC and restarted
with URL logging. Four searches. **Not a rate.**

## `probe-20261026T155534Z.jsonl` — 9 searches, killed by the memory-pressure reaper

The restarted probe: one search every 10 minutes, general and regulated-sector queries alternating,
planned for 14 hours. **Interrupted after 9 searches (15:55 to 17:23 UTC, 1.5 hours)**: Claude Code
stopped the background task because the machine was critically low on memory while the session was
idle. That is the harness protecting the system; it says nothing about the probe's own use of memory
(about 60 MB) and nothing about the engines. The remaining 12 hours were never measured. **Not a
rate.** What it does show, and no more:

| Engine | Named as failed | Contributed rows |
|---|---|---|
| brave | 0 of 9 | 9 of 9 |
| duckduckgo | 9 of 9 (CAPTCHA every time) | 0 |
| wikipedia | 6 of 9 (too many requests) | 0 of 9 |

Limits on reading it:
- Nine searches over 1.5 hours is a tenth of the intended run.
- At one search per 10 minutes the arithmetic maximum is 6 an hour. Brave answered 9 of 9, so its
  ceiling was never approached: the limit measured was the spacing, not the engine.
- Wikipedia not being named as failed in 3 searches does not mean it answered: it contributed zero
  rows in all nine.
- The gaps between searches were not exactly 10 minutes (16m19s, then 10m02s, ...). Rates are
  computed from the logged timestamps, never the nominal interval.

## `ramp-20261027T053618Z.jsonl` — the ramp: 20 searches, stopped, no summary line

Started 2026-10-27 05:36 UTC. **Twenty searches, 05:36:20 to 07:09:20; the file has no `summary` line**
because the process was stopped, not finished. The machine was suspended from about 07:10 and woke
at 08:29:37 UTC, which is when the scheduled 07:30 stop ran; the ramp made no asks after 07:09:20.
What the log shows, in order (T answered, X refused "too many requests"):

| Asks | 05:36–06:00 (5 min, then 4 min from 06:00) | 06:04 | 06:09, :14 | 06:19 | 06:24 – 07:09 (every 5 min) |
|---|---|---|---|---|---|
| Brave | 6 × T | X (the break) | X, X | T (26 rows) | 10 × X |

The registered test of the ten asks 06:44 to 07:29 (`docs/evaluation-plan.md` section 8) was
**incomplete**: six were made (all X) and four were not. Its verdict is "undecided". The ramp's
rule of two clean answers in a row was never met, so it never reported a recovery time. **Nothing in
this file is a rate that may be quoted.** Read the rest of this section for the format.

## `hold-20261027T144905Z.jsonl` — the first hold attempt: 2 searches, stopped by hand

The sustained hold (fixed 10-minute gaps, stop at the first refusal) started 14:49:05 UTC after
7.7 hours of silence. **Two searches, both answered by brave** (14:49:07, 22 rows; 15:17:33, 20 rows),
**28.4 minutes apart instead of 10**: the laptop was in Modern Standby for part of that interval and
the `late_ask` event records it (1706.5 s against a planned 600 s). I stopped it at about 15:25,
leaving no `summary` line, because the machine was on battery (19%, sleep after 180 s) and the run
could not be valid. It certifies nothing and would have finished at best as `held_with_gaps`. The
reason, the power setting that was changed, and how to restore it are in `Architecture.md` section 11,
"Worker-host power". The next hold attempt is a separate file.

## The ramp, as designed

`scripts/searxng_ramp_probe.py`: the gap between searches steps down 5, 4, 3, 2, 1.5, 1, 0.75, 0.5
minutes, five distinct searches at each, stopping at the first degradation of the steering engine
(brave only; duckduckgo and wikipedia never steer). It then backs off to the last fully clean level
and times the recovery. Its stepping logic is `app/research/rate_ramp.py`. Events (`level_start`,
`break`, `backoff`, `first_clean`, `recovered`) are lines of their own and the last line is a
`summary`. Its queries are distinct within the run; the regulated-group list is a fixed list, so
about five of its regulated queries also appeared in the 9-search run, more than twelve hours
earlier.

Read any of these with `uv run python scripts/summarise_search_rate.py <file>` from `backend/`
(the fixed-interval format) or by reading the `summary` line (the ramp).
