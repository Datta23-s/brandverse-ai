# SearXNG — local setup, what the real engine does, and what is still unverified

Verified 2026-10-26 on the laptop (Windows 11, Docker Desktop). The search provider
(`backend/app/research/search.py`, `SearxngSearch`) was written against a mock of SearXNG that was
the developer's own model of it. This records what the real engine does and where the model was
wrong. Nothing here is a result of the thesis; the engine set is a parameter of every search result
it reports (`Rules.md` A5).

## Setup (reproducible)

```
docker compose up -d searxng          # compose.yaml; settings in infra/searxng/settings.yml
SEARXNG_URL=http://127.0.0.1:8888     # .env; 127.0.0.1, not localhost (Architecture.md §11)
cd backend && PYTHONPATH=. uv run python scripts/searxng_probe.py --pause 20 [--errors]
```

| Pinned | Value |
|---|---|
| Image | `searxng/searxng:2026.10.19-81371861f` (a week old when chosen; `latest` is never used) |
| Digest | `sha256:5ed0323b5f5f8701264ef65e239a2bd83cb57098f2b971fa30bf79c06eeae271` |
| Reported by `/config` | `2026.10.19+81371861f` |
| Host port | **8888** (container 8080), bound to `127.0.0.1` only |

**Port.** 8080 is held on this laptop by another service (`httpd`), and Windows reserves ranges of
ports at random (`netsh interface ipv4 show excludedportrange protocol=tcp`; on this laptop 50000–50059,
50641–50740, … at the time of writing). 8888 was free and outside every range. If `docker compose up`
reports the port in use or "forbidden by its access permissions": list the excluded ranges, pick a port
outside them that nothing is listening on (`Get-NetTCPConnection -LocalPort <n>`), change the left side of
the mapping in `compose.yaml` and `SEARXNG_URL` together.

**Engines enabled** (read from `GET /config`, not from the settings file): **brave, duckduckgo,
wikipedia.** The first draft of the settings also listed `bing`; `/config` showed it present but
disabled (the image disables it by default), so the list suggested an engine that was not running. It
was removed, and `/config` now lists exactly these three, all enabled. The set is fixed by
`use_default_settings.engines.keep_only`; changing it changes the results and is recorded here.

**`json` must be in `search.formats`.** Without it the JSON API answers 403; `SearxngSearch` turns that
into a `SearchUnavailable` whose message names `search.formats`. Confirmed against the real engine
(`format=csv`, which is not enabled, answers 403).

## Where the real engine differed from the mock

1. **Throttled engines give HTTP 200 and no results, not an error.** This was the large one. When
   brave and wikipedia answer 429 to SearXNG and duckduckgo shows a CAPTCHA, SearXNG still answers
   200 with `"results": []` and names the failed engines in `unresponsive_engines`
   (`[["brave", "too many requests"], ["duckduckgo", "CAPTCHA"], ["wikipedia", "Suspended: too many
   requests"]]`). The provider reported that as "a search with no hits". A query whose results were
   all lost to throttling would have been recorded as the topic having no evidence. **Fixed:** zero
   results with any engine named is refused (`SearchRateLimited` if a reason says "too many requests",
   else `SearchUnavailable`); results with a failed engine are returned. Tests in
   `tests/test_research_search.py`. The mock now carries `unresponsive_engines` and the other fields
   below.
2. **Extra keys.** Top level: `answers, corrections, infoboxes, query, results, suggestions,
   unresponsive_engines`. Per row: `audio_src, author, category, close_group, content, engine, engines,
   iframe_src, img_src, length, metadata, open_group, parsed_url, positions, priority, pubdate,
   publishedDate, score, template, thumbnail, title, url, views`. The provider reads
   `url, title, content, engine, publishedDate` and ignores the rest, as designed; the mock now has the
   others so a change that starts depending on one has to say so.
3. **`publishedDate` is a naive ISO string** (`2026-04-28T18:09:17`, no zone) and often midnight
   (`2026-07-02T00:00:00`, a date only); between 22% and 91% of the rows of a response carried one
   (8 of 36, 21 of 25, 21 of 23, 11 of 25 in the four clean responses), so a missing date is common.
   The mock's `2026-10-01T00:00:00` was the right shape. Source scoring must treat midnight as a
   date, not a time, and a missing date as no recency (it already does).
4. **One page is 20–36 rows, not 10.** `limit` slices it. SearXNG merges the same URL across engines
   itself (no duplicate URLs seen); `engine` is the first engine and `engines` the list, so a row found by
   two engines is attributed to one in `SearchResult.engine`. Not yet seen in practice (no row had more
   than one) but possible when more engines answer.
5. **Row validity.** In the four clean responses no raw row lacked a title or content. Whether the
   provider ever drops a real row is **not established**: the first probe fetched each query twice (the
   second answer was often throttled), so its "parsed vs raw" counts compared different responses and
   are not evidence either way. The probe now reads the raw body of the very response it parsed.
   Some titles printed a replacement character on the Windows console; whether that is the console's
   encoding or the data was not separated.

## Error paths, provoked

| Provoked | Result |
|---|---|
| Closed port | `SearchUnavailable: SearXNG unreachable: ConnectError`, after **~2 s** (Windows retries a refused localhost connection; `127.0.0.1` does not avoid it) |
| Host that does not resolve | `SearchUnavailable … ConnectError`, 0.05 s |
| Wrong path (a 404) | `SearchUnavailable: SearXNG answered HTTP 404` |
| 1 ms client timeout | `SearchUnavailable … ReadTimeout`. The timeout belongs to the injected `httpx` client; the provider sets none. **Whoever builds the client must set one.** |
| `format` not enabled | HTTP 403 (the provider's message names `search.formats`) |
| SearXNG's own bot limiter (a scratch instance with `limiter: true` and Valkey, torn down after) | **A real HTTP 429**, body `Too Many Requests`; `SearchRateLimited` fired as designed |
| Upstream engines throttling (the normal case) | HTTP 200, empty list, `unresponsive_engines` — finding 1 |

The committed instance has the bot limiter **off** (it is bound to localhost and has one client), so
the 429 above cannot occur there. The rate limit that matters in practice is the upstream one, which
arrives as finding 1, never as a 429.

## Rate limiting: what actually happened

About ten searches in a few minutes — one query every few seconds from one residential IP — and
every enabled engine stopped answering: brave and wikipedia `too many requests` (SearXNG suspends
each for 180 s and the upstream answers 429 again as soon as it is retried), duckduckgo a CAPTCHA on
every attempt. **Twenty minutes later it had not recovered** with one query every 5 minutes. A
restart of the container does not help (the throttle is the upstream's, on this IP, not SearXNG's
state). Consequences, stated as open:

- **The 20-search latency exit criterion (`Phases.md` Phase 5) cannot be measured honestly against
  the live engines**: the test would measure the throttle. It needs pacing, or a measurement of
  the pipeline against recorded responses, with the live latency of a single search reported
  separately (a clean search took 1.0–1.5 s).
- **The thesis's evaluation search depends on engines that throttle a developer's laptop within
  minutes.** That is the reason for the record-and-replay snapshot ruling (owner, 2026-10-25), and
  it is now observed, not predicted. How many live searches a campaign needs, and whether this
  setup can serve them, is not known and must be measured before any evaluation run.
- The four searches in the first clean run are the only real responses recorded here; **no
  recorded fixture of a real response has been committed yet** (the throttle began before one was
  saved), so the mock's shape rests on what was printed, not on a replayed file. The probe script
  stores nothing.

## Not verified

- A real response recorded as a fixture (above).
- Any engine beyond brave, duckduckgo and wikipedia; whether any other reachable engine tolerates
  repeated use from this IP.
- Concurrent searches (the pipeline will issue several at once; the probe is sequential).
- Which engine a merged row comes from when two engines return it.
- Language: results appeared English; the queries were English.
