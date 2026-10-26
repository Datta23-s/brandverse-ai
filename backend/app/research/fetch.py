"""The page fetcher (Phases.md Phase 5; Architecture.md section 6): one URL in, its HTML out.

`PageFetcher.fetch(url)` returns a `FetchedPage` or raises one of three typed errors
(app/errors.py): `FetchRefused` (the fetcher's own policy), `FetchFailed` (the page was requested
and did not arrive), `FetchUnsupported` (it arrived and is not a page the pipeline takes). It does
not extract, score or cache: `Extractor` (extraction.py) turns the HTML into text, and the
zero-retention Redis cache sits above this (page_cache.py).

**The URLs come from a search engine and are untrusted.** A result can point at `localhost`, at a
router, at a cloud metadata address, or redirect there. The policy, applied to the URL and again to
every redirect target before anything is requested from it:
- the scheme is http or https, there are no credentials in the URL, and the port is 80 or 443;
- every address the host resolves to must be on the public internet (`ipaddress` `is_global`, and
  not multicast; an IPv4-mapped IPv6 address is judged as the IPv4 address it wraps). One private
  address among several refuses the host;
- redirects are followed by hand, at most `max_redirects`, never by the HTTP client.

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

Only `text/html` and `application/xhtml+xml` are taken. A PDF (a regulator's labelling circular is
often one) is `FetchUnsupported`: no PDF extraction exists. A response with no content type is
refused rather than sniffed.

Not done: `robots.txt`, per-host politeness delays, and character-set sniffing from `<meta>` (the
declared charset, else UTF-8 with replacement). Whether the fetcher should honour `robots.txt` is a
decision for the project owner, not a default made here.

`first_party` on the page is unpopulated (None) until Phase 6 supplies a brand domain; nothing reads
it (V6, docs/evaluation-plan.md).
"""

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from app.errors import FetchFailed, FetchRefused, FetchUnsupported
from app.memory.scoring import Research

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
ALLOWED_PORTS = {"http": 80, "https": 443}

# host, port -> the addresses it resolves to.
Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str  # as requested
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


class PageFetcher:
    """Fetches one page at a time through an injected `httpx.AsyncClient` and `Resolver`."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cfg: Research,
        *,
        resolver: Resolver = system_resolver,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client, self._cfg, self._resolver, self._clock = client, cfg, resolver, clock

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with asyncio.timeout(self._cfg.fetch_total_timeout_s):
                return await self._fetch(url)
        except TimeoutError as e:
            raise FetchFailed(
                f"timed out after {self._cfg.fetch_total_timeout_s:g}s in total"
            ) from e

    async def _fetch(self, url: str) -> FetchedPage:
        url = current = urldefrag(url.strip())[0]  # a fragment is never sent: not part of the page
        for hop in range(self._cfg.fetch_max_redirects + 1):
            await self._check(current)
            try:
                async with self._client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": self._cfg.fetch_user_agent, "Accept": "text/html"},
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
                    return await self._page(url, current, hop, response)
            except httpx.HTTPError as e:
                raise FetchFailed(f"{type(e).__name__} fetching {current}") from e
        raise FetchRefused(f"more than {self._cfg.fetch_max_redirects} redirects from {url}")

    async def _check(self, url: str) -> None:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_PORTS:
            raise FetchRefused(f"scheme {scheme!r} is not http or https: {url}")
        if parts.username is not None or parts.password is not None:
            raise FetchRefused(f"credentials in the URL: {parts.hostname}")
        host = parts.hostname
        if not host:
            raise FetchRefused(f"no host in {url}")
        try:
            port = parts.port or ALLOWED_PORTS[scheme]
        except ValueError as e:
            raise FetchRefused(f"invalid port in {url}") from e
        if port != ALLOWED_PORTS[scheme]:
            raise FetchRefused(f"port {port} is not the default for {scheme}: {host}")
        try:
            addresses = await self._resolver(host, port)
        except OSError as e:
            raise FetchFailed(f"{host} does not resolve") from e
        if not addresses:
            raise FetchFailed(f"{host} does not resolve")
        private = [a for a in addresses if not is_public(a)]
        if private:
            raise FetchRefused(f"{host} resolves to a non-public address ({private[0]})")

    async def _page(
        self, requested: str, final: str, hops: int, response: httpx.Response
    ) -> FetchedPage:
        status = response.status_code
        if not 200 <= status < 300:
            raise FetchFailed(f"HTTP {status} from {final}", status=status)
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media not in HTML_TYPES:
            raise FetchUnsupported(f"content type {media or 'missing'!r} is not HTML: {final}")
        cap = self._cfg.fetch_max_bytes
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cap:
            raise FetchUnsupported(f"declared {declared} bytes exceeds the cap of {cap}: {final}")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > cap:
                raise FetchUnsupported(f"body exceeds the cap of {cap} bytes: {final}")
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
