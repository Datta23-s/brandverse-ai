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

**Engine set widened 2026-10-28 (experiment, owner-ordered; NOT the declared set).** `infra/searxng/settings.yml`
now keeps 22 engines: the three above plus google, bing, yahoo, qwant, yep, mwmbl, mojeek, startpage, wiby,
dogpile, duckduckgo web, gmx, tusksearch, privacywall, searchmysite, swisscows, seznam, yandex, crowdview,
avalw. Marginalia was tried and dropped: the image refuses to register it without an API key. First search
(`FSSAI honey adulteration standards`): answered with rows brave, yep, yandex, swisscows, duckduckgo web,
yahoo, privacywall, gmx, mojeek, bing, avalw; failed duckduckgo (CAPTCHA), startpage (CAPTCHA), google,
qwant, dogpile, searchmysite (access denied), tusksearch (HTTP error), seznam (timeout); answered nothing
without an error crowdview, mwmbl, wiby, wikipedia. `research.declared_engines` was null at the time of this search and is now declared (2026-10-28, owner): yep, yandex, yahoo, duckduckgo web, with the evidence in `docs/evaluation-plan.md` section 8.


**Engines disabled 2026-10-28 (owner), before the recording.** google, qwant, startpage, dogpile, searchmysite
and tusksearch answered 0 of 8 searches in the burst and cost the 5 s timeout on each search, so they were
removed from `keep_only` and from the `engines:` block of `infra/searxng/settings.yml` (container restarted;
`GET /config` lists 17 enabled). brave and the other undeclared engines that answer stay. The engine set is part of
the evaluation's provenance, so it is fixed before the first recorded search; the cache key uses the DECLARED set, so
the 153 distinct searches are unchanged. avalw was disabled the same day for a different reason (below); the first change left the 5 s because avalw still timed out (it answered 3 of 8, so the
rule did not cover it), brave and wikipedia are suspended for too many requests, duckduckgo is behind a CAPTCHA.

**avalw disabled 2026-10-28 (owner), because of what its timeout did to a thesis metric:** it timed out on every search, so every search cost 5.03 s, and that is the unit cost the derived web time is computed from. Removed; `GET /config` lists 16 enabled; searches now take 3.0 to 3.6 s, the slowest answering engine. The unit cost is configuration-dependent (`docs/evaluation-plan.md` §8).

**Burst, 2026-10-28 02:55-03:09 UTC** (`evaluation/search_rate/engines-burst-20261028T025520Z.jsonl`; 8 searches at
2-minute gaps, run one minute after the single search above). Per-engine, searches answered with rows out
of 8: yep 8, yandex 8, yahoo 8, privacywall 8, duckduckgo web 8, swisscows 8, mojeek 8, bing 7, duckduckgo
(plain) 7, gmx 5, avalw 3, seznam 1; answered but thin (0-9 rows) mwmbl 7 of 8 with 1-9 rows, wiby 3 of 8
with 1 row. Failed every search: brave, google, qwant, dogpile, searchmysite, startpage, tusksearch. Rows per
search (engine attributions, not unique URLs; the log did not store URLs): 98-115.

**Read the failure counts with SearXNG's own suspension in mind.** After an engine error SearXNG stops
querying that engine for a time (`search.suspended_times` in the image's settings: 180 s for access denied
and for too many requests, 3600 s for CAPTCHA) and reports it as `Suspended: <reason>`. At 2-minute gaps a
failed engine is therefore asked again only every other search: brave was REFUSED (fresh) at searches 0, 2, 4,
6 and merely suspended at 1, 3, 5, 7. So brave's eight failures are four refusals, and google, qwant,
dogpile and searchmysite (access denied from the first search) are one refusal each per 180 s, not eight
independent ones. The earlier rate logs were at gaps of 4 minutes and more (above 180 s), and the only
`Suspended` in them is the first search of the 2026-10-26 15:16 run.

**Brave's budget.** Brave answered the single search at 02:54 (24 rows) after about 8.5 hours of silence and
refused every fresh request after it (02:55, 02:59, 03:03, 03:07). One answer then refusal at a 1-minute and
then 4-minute spacing is what a count-like limit predicts; it is one observation.

**Backend-grouping check (inference, not a backend map).** In the one search swisscows' 11 URLs were all in
brave's 23, and yahoo's 7 were all in duckduckgo web's 10. If swisscows were the same throttled upstream as
brave, it should fail when brave fails; it answered all 8 searches while brave was refused four times and
suspended four. So the containment seen in one search does not carry over to throttling. yahoo and
duckduckgo web never failed, so the burst cannot corroborate or refute that pair. Engines answering: 14 of
the 22 in at least one search; distinct backends: not established by this evidence.

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
   else `SearchUnavailable`). Results returned while some engines failed come back inside a
   `SearchResponse` that carries the failed engines with their reasons and `engines_seen` (the
   engines that contributed a row); `CachedSearch` keeps them per search, a reused entry keeps its
   fetch-time failures, and `SearchEvidenceReport` states `degraded_searches`, `failed_engine_counts`
   and `engines_seen`, so a run that saw one engine is told from one that saw three. Tests in
   `tests/test_research_search.py` and `tests/test_research_search_cache.py`. The mock now carries
   `unresponsive_engines` and the other fields below. **"Failed" is measured against the declared
   engine set** (owner, 2026-10-26; `docs/evaluation-plan.md` section 8): `research.declared_engines`
   is `null` (not declared) until the rate probe has shown which engines answer, and with a set
   declared only a failure of an engine in it degrades an answer or turns an empty one into a
   refusal; failures outside it are kept as `undeclared_failures`. If the set ends up being brave
   alone, a brave-only answer is complete for that declaration, and the narrower coverage and the
   single point of failure are named in the limitations.
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

## Fallback investigated, not approved: the Brave Search API

Costed on 2026-10-26 in case the throttle measurement (`docs/evaluation-plan.md` section 8) comes back
bad. **Nothing is built or approved.** Provenance: the figures below were read from Brave's own pages
through a summarising fetch tool and cross-checked against secondary pages that disagree with each
other; read the pricing page and the terms yourself before relying on any of it.

- **Free allowance.** Not the 2,000 queries a month older pages still quote. Brave's page lists a
  Search plan at "$5 per 1,000 requests" that "includes $5 in free credits every month" (about 1,000
  queries a month), a 50 queries-per-second limit, and, inconsistently, "up to $20 in free calls
  every month" at the top of the same page. Some sources say accounts from the earlier free plan are
  grandfathered at 2,000. Whichever it is, it is on the order of 1,000 to 2,000 queries a month.
- **A key and a card.** A key is required. The FAQ says a credit card is asked for "to prevent abuse"
  and usage past the credit is billed to it; no default spending cap was found. That sits badly
  with the zero-spend constraint: free only if the card is never charged, which needs a cap set on
  Brave's side that has not been checked.
- **The terms are the larger problem for this project.** Per the terms of service as fetched
  (Section 3(b)): no "store, cache, or create a database of Search Results" beyond transient
  operational storage; no retaining results after the agreement ends; no using results to "create,
  evaluate, train, re-train, fine-tune, benchmark" AI models or services; no derivative works. The page
  says storage needs "a plan that includes storage rights", without saying which. The evaluation's
  frozen snapshot is a stored database of search results, and the thesis evaluates a system built on
  them, so **the standard terms appear to forbid exactly what the snapshot ruling needs.** This
  could only be settled by reading the terms and asking Brave, and it may rule the API out regardless
  of the quota.
- **Cost to add it as a second `SearchProvider`.** Small: one class beside `SearxngSearch` doing a
  GET with the key in a header, a 429 mapped to `SearchRateLimited`, rows mapped to `SearchResult`
  (the endpoint path, the `X-Subscription-Token` header and the `count` limit of 20 are from memory,
  not checked this session), a key in `Settings`, and contract tests over a mock transport. Roughly
  a day including a real call. Its answer would have no `unresponsive_engines`; its failure mode is
  a plain 429, which the provider interface already handles.

## Not verified

- A real response recorded as a fixture (above).
- Any engine beyond brave, duckduckgo and wikipedia; whether any other reachable engine tolerates
  repeated use from this IP.
- Concurrent searches (the pipeline will issue several at once; the probe is sequential).
- Which engine a merged row comes from when two engines return it.
- Language: results appeared English; the queries were English.
