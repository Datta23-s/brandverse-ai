# The page fetcher and page cache — what they do, and what the real web showed

Built and run against the real network 2026-10-26. Code: `backend/app/research/fetch.py`,
`page_cache.py`. Tests: `tests/test_research_fetch.py` (68, over a mock web and a fake resolver) and
`tests/test_research_page_cache.py` (15, one against a real Redis). Real-network run:
`backend/scripts/fetch_probe.py`.

## What it does

`PageFetcher.fetch(url)` returns a `FetchedPage` (the URL as requested without its fragment, the final
URL, status, media type, decoded HTML, fetch time, redirect count, and the unpopulated `first_party`
marker) or raises one of three typed errors:

| Error | Meaning |
|---|---|
| `FetchRefused` | The fetcher's own policy said no, before anything was requested from the refused page. `kind`: `scheme`, `credentials`, `host`, `port`, `address`, `redirects`, `robots` |
| `FetchFailed` | The page was requested and did not arrive, or permission could not be established: network failure, timeout, no DNS answer, an error status (`.status`), robots.txt unreachable. `kind`: `network`, `timeout`, `dns`, `http_status`, `robots_unavailable` |
| `FetchUnsupported` | It arrived, or its URL says, that it is not taken. `kind`: `pdf`, `not_html`, `no_content_type`, `too_large` |

Every error carries its `kind`, and the page cache's report counts failures by `Class:kind`
(`FetchUnsupported:pdf`, `FetchRefused:robots`), so what was skipped, and why, can be read off a run.

## robots.txt is honoured (owner, 2026-10-26)

A site that disallows crawling and is crawled anyway can block this IP, which already has a
reputation problem, and "we ignored robots.txt" is not a sentence for an ethics section. Before a
page is requested, and before each redirect target, the origin's `/robots.txt` is fetched once and
kept `research.robots_ttl_s` (one fetch per origin however many pages, concurrent fetches
included), and read **as RFC 9309 reads it** (`app/research/robots.py`): our own agent group
(`BrandverseResearch`, the User-Agent's first word) if there is one, else `*`; the **longest**
matching pattern wins and `Allow` wins a tie; `*` and a trailing `$` work; the query string is part
of the path. The standard library's `robotparser` was not used because it applies the first
matching rule, and reads `Disallow: /` then `Allow: /public/` the opposite way (a test shows it).
Per the RFC: a 4xx robots.txt means "none, everything allowed"; a **server error or an
unreachable server is not permission** (`FetchFailed`, `robots_unavailable`, not cached, so the next
page retries); a robots.txt that redirects more than the limit counts as unavailable. robots.txt is
fetched under the same address policy as pages. Checked against Google's real file: `/search` is
refused without being requested, `/about/` is fetched. Not done: `Crawl-delay` (per-host spacing is
done, below).

## PDFs: skipped on purpose, and measured (owner, 2026-10-26)

A URL whose path ends in `.pdf` is `FetchUnsupported`/`pdf` **without being requested** (not even
its robots.txt), and a response of type `application/pdf` is the same, so a regulator's circular is
a counted skip, not an opaque failure or extracted garbage. There is no PDF extraction. Whether
that matters is measured, cheaply, because the search engines already throttle this IP:
`scripts/classify_result_types.py` counts `.pdf` paths in the logged result URLs (free: no request),
and asks **HEAD, never GET** (robots.txt honoured, no fallback to GET) for a seeded random sample of
50 of the *regulated* group's remaining URLs only; the general group is reported as its free floor.
The result is an estimate with its 95% interval. Run when the rate ramp has finished and the URL log
is fuller; the owner rules again if it is a real gap (`docs/evaluation-plan.md` section 8).

`CachedPages` puts a Redis cache above it: the page body under `research.page_cache_ttl_s`, **a
parameter with no default in the code**, an entry older than the TTL in force a miss, a ledger and
report of fetched / reused / failed (by error kind) per run, errors never cached, a Redis error a
counted miss. Nothing in either module can write to Postgres (a test reads the source for it).

## The address policy (the URLs are untrusted)

URLs come from a search engine. A result can point at `localhost` or a cloud metadata address, or
redirect there. Applied to the URL and to **every redirect target before it is requested**: http or
https only; no credentials in the URL; port 80 or 443 only; every address the host resolves to must be
public (`is_global`, not multicast, an IPv4-mapped IPv6 address judged as its IPv4), so one private
address among several refuses the host. Redirects are followed by hand, at most
`research.fetch_max_redirects`. The tests assert that nothing is requested from a refused address, and
a mutation check (every address "public") failed 21 of them.

**Residual, not closed: DNS rebinding.** The host is resolved to be checked and resolved again by the
HTTP client to connect. A host that answers differently the second time passes. Closing it needs the
connection pinned to the checked address, which breaks TLS name checking in this client. Recorded, not
assumed away.

## Limits (all `config/scoring.yaml` `research.fetch_*`, untuned defaults)

10 s per network operation, 20 s for the whole fetch (a slow drip cannot hold it), 2,000,000 bytes
counted on the decoded body (a compression bomb stops at the cap; tested with a ~300-byte gzip that
decodes to 1 MB), 5 redirects. Over the cap is refused, not truncated. User agent:
`BrandverseResearch/0.1 (academic research project)` — no personal address in a header sent to other
people's servers.

## What the real web showed (2026-10-26, `scripts/fetch_probe.py`)

Thirteen cases, real DNS and TLS, then again through the cache against the local Redis:
- Plain pages, `http` to `https` upgrade (one redirect), a fragment, a 404 (`FetchFailed`, status 404),
  a PDF (`FetchUnsupported`, `application/pdf`), a name that does not exist (`FetchFailed`, no
  resolve), `localhost` / `127.0.0.1` / `169.254.169.254` / a non-default port / `ftp:` (`FetchRefused`):
  all as designed. The second pass reused the five URLs that had been fetched (0.00–0.01 s; four
  distinct pages, the fragment case being the same page) and re-tried each refusal and failure, since
  none is cached.
- **A client-rendered page fetches with 200 and has no text.** `https://www.fssai.gov.in/` returns about
  4,000 characters: a shell with five script tags and no paragraph. The extractor correctly refuses it
  (`ExtractionFailed: no main content`). A plain HTTP fetcher cannot read such sites, and a brand's own
  site built as a single-page application may be one: for those, the pipeline gets nothing, and the run
  should record that the page was unreadable rather than that it had no content. Rendering JavaScript
  (a headless browser) is a different dependency and is not proposed here.
- The mock web would not have shown that: it served the HTML it was given.

## Per-host politeness (added 2026-10-28)

`research.fetch_host_delay_s` (1.0 s, not tuned): at least that long between two requests the fetcher
STARTS to the same host, whatever they are for (a page, its robots.txt, each redirect hop). Hosts do not
wait for each other; concurrent fetches of one host take consecutive slots. It is a property of how the
fetcher treats a server (the same obligation as honouring robots.txt), not a planning parameter, and
`0` turns it off. The wait is inside `fetch_total_timeout_s`, so a long queue on one host can time out;
the planner's cap per host is what prevents that. `waits` / `waited_s` count what it cost. Tests:
`tests/test_research_fetch.py`, "Per-host politeness" (5), on a fake monotonic clock.

## Fetch cost of one real search (measured 2026-10-28)

`scripts/fetch_cost_probe.py`, log `evaluation/fetch_cost/fetch-20261028T033217Z.jsonl`. Input: the 22-engine
search for "FSSAI honey adulteration standards" (81 merged results; 79 distinct URLs by this script's
trailing-slash rule, 61 hosts). Real network, robots.txt honoured, politeness 1 s, concurrency 8, one pass,
pages read and dropped.

| | |
|---|---|
| Outcomes | 67 fetched, 12 PDFs skipped unrequested (15%), 0 robots refusals, 0 failures |
| robots.txt fetched | 62 (one per origin) |
| Wall clock at concurrency 8 | 23.8 s |
| Sequential estimate | 167 s (sum of per-URL times) plus 41 s of politeness waits = about 3.5 min |
| Per URL | median 1.9 s, p90 4.1 s, slowest 7.5 s |
| Largest host | fssai.gov.in, 11 pages: at least 11 s of politeness on that host alone |
| Median fetched body | 217,201 characters |

So the "about 26 minutes sequential" worked out earlier was the timeout ceiling (77 x 20 s), not the
cost: the cost of this search is minutes sequentially, tens of seconds concurrently. One search, one
query, one day: it is the number to set the planner's cap against, not a distribution. Extraction
(trafilatura, CPU) and the memory the pages occupy are not in it. The PDF share here (12 of 79) is
higher than the 8.3% regulated floor measured earlier from extensions alone.

## Not done, and why

- **`Crawl-delay`**: robots.txt is honoured, its crawl-delay is not; the configured per-host delay applies whatever a file says.
- **PDFs** are unsupported (regulators publish circulars as PDFs; a search for FSSAI labelling returned
  one as its second result in the first real probe); skipped visibly, share to be measured, above.
- **Charset sniffing** from `<meta>`: the declared charset is used, else UTF-8 with replacement.
- **Concurrency and the cap on pages fetched**: one page per call; the planner decides how many at once and how many results to fetch (owner, 2026-10-28: the cap belongs to the planner, not here).
- **JavaScript-rendered pages**: above.
