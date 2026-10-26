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
| `FetchRefused` | The fetcher's own policy said no, before anything was requested from the refused address |
| `FetchFailed` | The page was requested and did not arrive: network failure, timeout, no DNS answer, an error status (`.status`) |
| `FetchUnsupported` | It arrived and is not taken: not HTML, no content type, a body over the byte cap |

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

## Not done, and why

- **`robots.txt`** is not read or honoured, and there is no per-host delay. Whether a research fetcher
  should honour `robots.txt` is a decision for the project owner; this records that it does not yet.
- **PDFs** are unsupported (regulators publish circulars as PDFs; a search for FSSAI labelling returned
  one as its second result in the first real probe).
- **Charset sniffing** from `<meta>`: the declared charset is used, else UTF-8 with replacement.
- **Concurrency and per-host politeness**: one page per call; the planner decides how many at once.
- **JavaScript-rendered pages**: above.
