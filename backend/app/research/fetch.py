"""The page fetcher (Phases.md Phase 5; Architecture.md section 6): one URL in, its HTML out.

`PageFetcher.fetch(url)` returns a `FetchedPage` or raises one of three typed errors
(app/errors.py), each with a `kind` that says why: `FetchRefused` (the fetcher's own policy),
`FetchFailed` (the page was requested and did not arrive, or permission could not be established),
`FetchUnsupported` (not something the pipeline takes as a page). It does not extract, score or
cache:
`Extractor` (extraction.py) turns the HTML into text, and the zero-retention Redis cache sits above
this (page_cache.py).

**The URLs come from a search engine and are untrusted.** A result can point at `localhost`, at a
router, at a cloud metadata address, or redirect there. The policy, applied to the URL and again to
every redirect target before anything is requested from it, and to `robots.txt` itself:
- the scheme is http or https, there are no credentials in the URL, and the port is 80 or 443;
- every address the host resolves to must be on the public internet (`ipaddress` `is_global`, and
  not multicast; an IPv4-mapped IPv6 address is judged as the IPv4 address it wraps). One private
  address among several refuses the host;
- redirects are followed by hand, at most `max_redirects`, never by the HTTP client.

**robots.txt is honoured** (owner, 2026-10-26: a site that disallows crawling and is crawled anyway
can block this IP, which already has a reputation problem, and "we ignored robots.txt" does not
belong in an ethics section). Before a page is requested, and before each redirect target, the
origin's `/robots.txt` is fetched (once per origin, kept `research.robots_ttl_s`) and read as RFC
9309 reads it (robots.py). The agent token is the first word of the configured User-Agent. Per the
RFC: 2xx, parsed; 4xx, the file is unavailable and everything is allowed; a server error or an
unreachable server is NOT treated as permission (`FetchFailed`, kind `robots_unavailable`, not
cached, so the next page retries); more than the redirect limit on robots.txt itself counts as
unavailable-and-allowed. Only the first `robots_max_bytes` of a file are read. A disallowed page is
`FetchRefused` with kind `robots` and is never requested. Not done: `Crawl-delay` and per-host
request spacing (the planner decides how many fetches run at once).

**Residual, stated: DNS rebinding.** The host is resolved to check it and the HTTP client resolves
it again to connect, so a host that answers differently the second time passes the check. Closing it
needs the connection pinned to the checked address, which breaks TLS server-name checking in this
client. Not closed; the fetcher runs on a laptop with nothing on its network that the project's
secrets depend on beyond the loopback services this guard also covers, and the risk is recorded here
rather than assumed away.

**Limits, all configuration** (`research.fetch_*` in config/scoring.yaml): a timeout per network
operation, a total timeout for the whole fetch (so a slow drip cannot hold it), a byte cap counted
on the DECODED body (a compression bomb stops at the cap), and a redirect cap. A body over the
cap is refused, not truncated: half a page is not a page.

Only `text/html` and `application/xhtml+xml` are taken. **A PDF is skipped on purpose and visibly**
(owner, 2026-10-26): a URL whose path ends in `.pdf` is `FetchUnsupported` kind `pdf` without being
requested, and a response of type `application/pdf` is the same, so a regulator's circular is a
counted skip, not an opaque failure or garbage text. No PDF extraction exists. Any other type is
kind
`not_html`; a response with no content type is kind `no_content_type`, refused rather than sniffed.

**Per-host politeness** (`research.fetch_host_delay_s`): at least that long between two requests
this fetcher STARTS to the same host (a page, its robots.txt and each redirect hop all count), so a
result list with several pages on one site is spread out instead of hitting it in a burst. Hosts do
not wait for each other, and concurrent fetches of one host queue in order of arrival. The wait is
part of `fetch_total_timeout_s`: a long queue on one host can time out (kind `timeout`), which the
planner avoids by capping pages per host. `waits` and `waited_s` count what politeness cost.
Not done: `Crawl-delay` from robots.txt (the configured delay applies whatever it says) and
character-set sniffing from `<meta>` (the declared charset, else UTF-8 with replacement).

`first_party` on the page is unpopulated (None) until Phase 6 supplies a brand domain; nothing reads
it (V6, docs/evaluation-plan.md).
"""

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from app.errors import FetchFailed, FetchRefused, FetchUnsupported
from app.memory.scoring import Research
from app.research.robots import RobotsRules, agent_token

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
ALLOWED_PORTS = {"http": 80, "https": 443}

T = TypeVar("T")

# host, port -> the addresses it resolves to.
Resolver = Callable[[str, int], Awaitable[list[str]]]


class _RobotsRedirectLimit(FetchRefused):
    """robots.txt itself redirected more than the limit: the RFC treats that as unavailable."""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str  # as requested, without its fragment
    final_url: str  # after redirects
    status: int
    content_type: str  # the media type only, lower-cased
    html: str
    fetched_at: float  # epoch seconds
    redirects: int
    first_party: bool | None = None  # UNPOPULATED until Phase 6; nothing reads it (V6)


class PageSource(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return sorted({str(info[4][0]) for info in infos})


def is_public(address: str) -> bool:
    """Whether an address is on the public internet. Anything unparseable is not."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # an IPv6 zone id is not an address
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.casefold().endswith(".pdf")


class PageFetcher:
    """Fetches one page at a time through an injected `httpx.AsyncClient` and `Resolver`. One
    instance per run: it holds the per-origin robots.txt cache."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cfg: Research,
        *,
        resolver: Resolver = system_resolver,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client, self._cfg, self._resolver, self._clock = client, cfg, resolver, clock
        self._monotonic, self._sleep = monotonic, sleep
        self._next_start: dict[str, float] = {}  # host -> earliest next request start (monotonic)
        self.waits = 0  # requests that had to wait for their host's turn
        self.waited_s = 0.0  # the total time they waited
        self._agent = agent_token(cfg.fetch_user_agent)
        self._robots: dict[str, tuple[RobotsRules, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.robots_fetches = 0  # how many robots.txt files were requested (not cache hits)

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with asyncio.timeout(self._cfg.fetch_total_timeout_s):
                return await self._fetch(url)
        except TimeoutError as e:
            raise FetchFailed(
                f"timed out after {self._cfg.fetch_total_timeout_s:g}s in total", kind="timeout"
            ) from e

    async def content_type_of(self, url: str) -> str:
        """The media type a URL answers with, asked with HEAD so that no body is ever sent (owner,
        2026-10-26: the IP cannot afford more), under the same address policy, robots.txt and
        redirect handling as `fetch`. For measuring what share of search results the pipeline
        cannot take (scripts/classify_result_types.py); a `.pdf` path is NOT special-cased here, so
        the answer is what the server says. A server that refuses HEAD (405, 501) is `FetchFailed`
        kind `head_not_supported`: there is deliberately NO fallback to GET, so such a URL is
        counted as unknown, not fetched. Raises the same typed errors as `fetch` otherwise."""
        url = urldefrag(url.strip())[0]

        async def media(current: str, hops: int, response: httpx.Response) -> str:
            if response.status_code in (405, 501):
                raise FetchFailed(
                    f"HEAD not supported by {current}",
                    status=response.status_code,
                    kind="head_not_supported",
                )
            if not 200 <= response.status_code < 300:
                raise FetchFailed(
                    f"HTTP {response.status_code} from {current}",
                    status=response.status_code,
                    kind="http_status",
                )
            return str(response.headers.get("content-type", "")).split(";", 1)[0].strip().lower()

        try:
            async with asyncio.timeout(self._cfg.fetch_total_timeout_s):
                return await self._walk(url, media, check_robots=True, method="HEAD")
        except TimeoutError as e:
            raise FetchFailed("timed out", kind="timeout") from e

    async def _fetch(self, url: str) -> FetchedPage:
        url = urldefrag(url.strip())[0]  # a fragment is never sent: not part of the page
        if is_pdf_url(url):
            raise FetchUnsupported(f"a PDF, not supported (skipped unrequested): {url}", kind="pdf")

        async def page(current: str, hops: int, response: httpx.Response) -> FetchedPage:
            return await self._page(url, current, hops, response)

        return await self._walk(url, page, check_robots=True)

    async def _walk(
        self,
        start: str,
        on_response: Callable[[str, int, httpx.Response], Awaitable[T]],
        *,
        check_robots: bool,
        accept: str = "text/html",
        method: str = "GET",
        redirect_limit_error: type[FetchRefused] = FetchRefused,
    ) -> T:
        """Request `start` (GET unless told otherwise), following redirects by hand, each hop judged
        by the address policy (and, for pages, by robots.txt) before anything is requested."""
        current = start
        for hop in range(self._cfg.fetch_max_redirects + 1):
            await self._check(current)
            if check_robots:
                await self._require_robots_permit(current)
            await self._wait_for_turn(current)
            try:
                async with self._client.stream(
                    method,
                    current,
                    headers={"User-Agent": self._cfg.fetch_user_agent, "Accept": accept},
                    timeout=self._cfg.fetch_timeout_s,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailed(
                                f"HTTP {response.status_code} without a Location",
                                status=response.status_code,
                            )
                        current = urldefrag(urljoin(current, location))[0]
                        continue
                    return await on_response(current, hop, response)
            except httpx.HTTPError as e:
                raise FetchFailed(f"{type(e).__name__} fetching {current}", kind="network") from e
        raise redirect_limit_error(
            f"more than {self._cfg.fetch_max_redirects} redirects from {start}", kind="redirects"
        )

    async def _wait_for_turn(self, url: str) -> None:
        """Reserve this request's start slot on its host, then wait for it. The slot is taken before
        any await, so concurrent callers get consecutive slots rather than all the same one."""
        delay = self._cfg.fetch_host_delay_s
        if delay <= 0:
            return
        host = (urlsplit(url).hostname or "").lower()
        now = self._monotonic()
        slot = max(now, self._next_start.get(host, now))
        self._next_start[host] = slot + delay
        if slot > now:
            self.waits += 1
            self.waited_s += slot - now
            await self._sleep(slot - now)

    async def _check(self, url: str) -> None:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_PORTS:
            raise FetchRefused(f"scheme {scheme!r} is not http or https: {url}", kind="scheme")
        if parts.username is not None or parts.password is not None:
            raise FetchRefused(f"credentials in the URL: {parts.hostname}", kind="credentials")
        host = parts.hostname
        if not host:
            raise FetchRefused(f"no host in {url}", kind="host")
        try:
            port = parts.port or ALLOWED_PORTS[scheme]
        except ValueError as e:
            raise FetchRefused(f"invalid port in {url}", kind="port") from e
        if port != ALLOWED_PORTS[scheme]:
            raise FetchRefused(f"port {port} is not the default for {scheme}: {host}", kind="port")
        try:
            addresses = await self._resolver(host, port)
        except OSError as e:
            raise FetchFailed(f"{host} does not resolve", kind="dns") from e
        if not addresses:
            raise FetchFailed(f"{host} does not resolve", kind="dns")
        private = [a for a in addresses if not is_public(a)]
        if private:
            raise FetchRefused(
                f"{host} resolves to a non-public address ({private[0]})", kind="address"
            )

    # ── robots.txt ───────────────────────────────────────────────────────────────────────────

    async def _require_robots_permit(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        rules = await self._rules_for(origin)
        if not rules.allows(url):
            raise FetchRefused(
                f"robots.txt at {origin} disallows {url} for {self._agent}", kind="robots"
            )

    async def _rules_for(self, origin: str) -> RobotsRules:
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:  # two pages of one host fetch one file, not two
            cached = self._robots.get(origin)
            if cached is not None and self._clock() - cached[1] <= self._cfg.robots_ttl_s:
                return cached[0]
            rules = await self._fetch_robots(origin)
            self._robots[origin] = (rules, self._clock())
            return rules

    async def _fetch_robots(self, origin: str) -> RobotsRules:
        self.robots_fetches += 1

        async def read(current: str, hops: int, response: httpx.Response) -> RobotsRules:
            status = response.status_code
            if 400 <= status < 500:
                return RobotsRules.allow_all()  # unavailable: RFC 9309 allows everything
            if not 200 <= status < 300:
                raise FetchFailed(
                    f"robots.txt at {origin} answered HTTP {status}: permission not established",
                    status=status,
                    kind="robots_unavailable",
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) >= self._cfg.robots_max_bytes:
                    break  # the RFC requires at least 500 KiB; the rest is ignored
            text = bytes(body[: self._cfg.robots_max_bytes]).decode("utf-8", errors="replace")
            return RobotsRules.parse(text, self._agent)

        try:
            return await self._walk(
                f"{origin}/robots.txt",
                read,
                check_robots=False,
                accept="text/plain",
                redirect_limit_error=_RobotsRedirectLimit,
            )
        except _RobotsRedirectLimit:
            return RobotsRules.allow_all()
        except FetchFailed as e:
            if e.kind == "robots_unavailable":
                raise
            raise FetchFailed(
                f"robots.txt at {origin} could not be retrieved ({e}): permission not established",
                status=e.status,
                kind="robots_unavailable",
            ) from e
        except FetchRefused as e:  # robots.txt redirected somewhere the address policy refuses
            raise FetchFailed(
                f"robots.txt at {origin} could not be retrieved ({e}): permission not established",
                kind="robots_unavailable",
            ) from e

    # ── the page ─────────────────────────────────────────────────────────────────────────────

    async def _page(
        self, requested: str, final: str, hops: int, response: httpx.Response
    ) -> FetchedPage:
        status = response.status_code
        if not 200 <= status < 300:
            raise FetchFailed(f"HTTP {status} from {final}", status=status, kind="http_status")
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media == "application/pdf":
            raise FetchUnsupported(f"a PDF, not supported: {final}", kind="pdf")
        if not media:
            raise FetchUnsupported(f"no content type declared: {final}", kind="no_content_type")
        if media not in HTML_TYPES:
            raise FetchUnsupported(f"content type {media!r} is not HTML: {final}", kind="not_html")
        cap = self._cfg.fetch_max_bytes
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cap:
            raise FetchUnsupported(
                f"declared {declared} bytes exceeds the cap of {cap}: {final}", kind="too_large"
            )
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > cap:
                raise FetchUnsupported(
                    f"body exceeds the cap of {cap} bytes: {final}", kind="too_large"
                )
        encoding = response.charset_encoding or "utf-8"
        try:
            html = bytes(body).decode(encoding, errors="replace")
        except LookupError:  # a declared charset Python does not know
            html = bytes(body).decode("utf-8", errors="replace")
        return FetchedPage(
            url=requested,
            final_url=final,
            status=status,
            content_type=media,
            html=html,
            fetched_at=self._clock(),
            redirects=hops,
        )
