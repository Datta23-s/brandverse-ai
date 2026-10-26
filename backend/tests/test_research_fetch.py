"""The page fetcher (app/research/fetch.py; Phases.md Phase 5).

Nothing touches the network: an httpx mock transport plays the web and a fake resolver plays DNS.
Every refusal test also asserts that NOTHING was requested from the refused address, which is the
point of the policy.
"""

import asyncio
import gzip
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.errors import FetchError, FetchFailed, FetchRefused, FetchUnsupported
from app.memory.scoring import Research, load_scoring
from app.research.fetch import FetchedPage, PageFetcher, is_public

pytestmark = pytest.mark.anyio

CONFIG = load_scoring().research
HTML = "<html><head><title>T</title></head><body><p>Hello kadai</p></body></html>"
PAGE_URL = "https://shop.example.test/products/kadai"
DNS = {
    "shop.example.test": ["93.184.216.34"],
    "other.example.test": ["93.184.216.35"],
    "loopback.example.test": ["127.0.0.1"],
    "internal.example.test": ["10.0.0.7"],
    "metadata.example.test": ["169.254.169.254"],
    "mixed.example.test": ["93.184.216.34", "192.168.1.5"],
    "v6loop.example.test": ["::1"],
    "mapped.example.test": ["::ffff:127.0.0.1"],
}


def html_response(body: str = HTML, **headers: str) -> httpx.Response:
    return httpx.Response(
        200, content=body.encode("utf-8"), headers={"content-type": "text/html", **headers}
    )


class Web:
    """A mock web: records every request, answers with `handler`."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler, self.seen = handler, []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return self.handler(request)

    @property
    def hosts(self) -> list[str]:
        return [r.url.host for r in self.seen]


async def resolver(host: str, port: int) -> list[str]:
    if host in DNS:
        return DNS[host]
    if host == "gone.example.test":
        raise OSError("name or service not known")
    return [host]  # an IP literal resolves to itself, as getaddrinfo does


def fetcher(
    handler: Callable[[httpx.Request], httpx.Response] = lambda _: html_response(),
    **cfg: object,
) -> tuple[PageFetcher, Web]:
    web = Web(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(web))
    config: Research = CONFIG.model_copy(update=cfg)
    return PageFetcher(client, config, resolver=resolver, clock=lambda: 1_700_000_000.0), web


# ── A page ───────────────────────────────────────────────────────────────────────────────────


async def test_a_page_comes_back_with_what_the_pipeline_needs_to_know() -> None:
    f, web = fetcher()
    page = await f.fetch(PAGE_URL + "#reviews")
    assert page == FetchedPage(
        url=PAGE_URL,  # the fragment is never sent, so it is not part of the request either
        final_url=PAGE_URL,
        status=200,
        content_type="text/html",
        html=HTML,
        fetched_at=1_700_000_000.0,
        redirects=0,
        first_party=None,
    )
    (request,) = web.seen
    assert str(request.url) == PAGE_URL
    assert request.headers["user-agent"] == CONFIG.fetch_user_agent
    assert "kundansable" not in request.headers["user-agent"]  # no personal address in a header


async def test_first_party_is_unpopulated_and_the_fetcher_never_reads_it() -> None:
    page = await fetcher()[0].fetch(PAGE_URL)
    assert page.first_party is None


@pytest.mark.parametrize(
    "content_type", ["text/html; charset=utf-8", "TEXT/HTML", "application/xhtml+xml"]
)
async def test_html_content_types_are_taken(content_type: str) -> None:
    f, _ = fetcher(lambda _: html_response(**{"content-type": content_type}))
    assert (await f.fetch(PAGE_URL)).html == HTML


@pytest.mark.parametrize(
    "content_type", ["application/pdf", "image/png", "application/json", "text/plain"]
)
async def test_what_is_not_html_is_unsupported_not_a_crash(content_type: str) -> None:
    f, _ = fetcher(
        lambda _: httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": content_type})
    )
    with pytest.raises(FetchUnsupported, match="not HTML"):
        await f.fetch(PAGE_URL)


async def test_a_response_with_no_content_type_is_refused_not_sniffed() -> None:
    f, _ = fetcher(
        lambda _: httpx.Response(200, content=HTML.encode(), headers={"content-type": ""})
    )
    with pytest.raises(FetchUnsupported, match="missing"):
        await f.fetch(PAGE_URL)


async def test_the_declared_charset_is_used_and_a_bad_one_falls_back_to_utf8() -> None:
    latin = "<p>café kadai</p>".encode("latin-1")
    f, _ = fetcher(
        lambda _: httpx.Response(
            200, content=latin, headers={"content-type": "text/html; charset=iso-8859-1"}
        )
    )
    assert "café" in (await f.fetch(PAGE_URL)).html
    f, _ = fetcher(
        lambda _: httpx.Response(
            200, content=b"<p>ok \xff\xfe</p>", headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    assert "ok" in (await f.fetch(PAGE_URL)).html  # undecodable bytes are replaced, never a crash
    f, _ = fetcher(
        lambda _: httpx.Response(
            200,
            content=b"<p>ok</p>",
            headers={"content-type": "text/html; charset=no-such-charset"},
        )
    )
    assert (await f.fetch(PAGE_URL)).html == "<p>ok</p>"


# ── Redirects: followed by hand, each hop judged ─────────────────────────────────────────────


async def test_a_redirect_is_followed_and_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/products/kadai#top"})
        return html_response()

    f, web = fetcher(handler)
    page = await f.fetch("https://shop.example.test/old")
    assert (page.url, page.final_url, page.redirects) == (
        "https://shop.example.test/old",
        PAGE_URL,
        1,
    )
    assert [r.url.path for r in web.seen] == ["/old", "/products/kadai"]


async def test_a_redirect_to_a_non_public_host_is_refused_before_it_is_requested() -> None:
    f, web = fetcher(
        lambda _: httpx.Response(302, headers={"location": "https://internal.example.test/admin"})
    )
    with pytest.raises(FetchRefused, match="non-public"):
        await f.fetch(PAGE_URL)
    assert web.hosts == ["shop.example.test"]  # the internal host was never contacted


async def test_a_redirect_to_another_scheme_or_port_is_refused() -> None:
    for target in (
        "ftp://shop.example.test/x",
        "https://shop.example.test:8443/x",
        "file:///etc/passwd",
    ):
        f, web = fetcher(lambda _, t=target: httpx.Response(302, headers={"location": t}))
        with pytest.raises(FetchRefused):
            await f.fetch(PAGE_URL)
        assert len(web.seen) == 1


async def test_too_many_redirects_is_refused() -> None:
    f, web = fetcher(
        lambda r: httpx.Response(302, headers={"location": r.url.path + "x"}), fetch_max_redirects=3
    )
    with pytest.raises(FetchRefused, match="more than 3 redirects"):
        await f.fetch(PAGE_URL)
    assert len(web.seen) == 4  # the original request and three redirects, then it stops


async def test_a_redirect_with_no_location_is_a_failure_with_its_status() -> None:
    f, _ = fetcher(lambda _: httpx.Response(302))
    with pytest.raises(FetchFailed) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.status == 302


# ── The address policy ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "ftp://shop.example.test/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "//shop.example.test/x",
        "https://user:secret@shop.example.test/x",
        "https://shop.example.test:8443/x",
        "http://shop.example.test:22/x",
        "https:///nohost",
        "https://shop.example.test:notaport/x",
    ],
)
async def test_urls_the_policy_does_not_allow_are_refused_without_a_request(url: str) -> None:
    f, web = fetcher()
    with pytest.raises(FetchRefused):
        await f.fetch(url)
    assert web.seen == []


@pytest.mark.parametrize(
    "url",
    [
        "https://loopback.example.test/",
        "https://internal.example.test/",
        "https://metadata.example.test/latest/meta-data/",
        "https://mixed.example.test/",  # one private address among public ones refuses the host
        "https://v6loop.example.test/",
        "https://mapped.example.test/",  # ::ffff:127.0.0.1 is 127.0.0.1
        "http://127.0.0.1/",
        "http://169.254.169.254/",
        "http://10.1.2.3/",
        "http://192.168.0.1/",
        "http://172.16.0.1/",
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://224.0.0.1/",  # multicast
    ],
)
async def test_an_address_that_is_not_on_the_public_internet_is_refused_without_a_request(
    url: str,
) -> None:
    f, web = fetcher()
    with pytest.raises(FetchRefused, match="non-public"):
        await f.fetch(url)
    assert web.seen == []


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:93.184.216.34", True),
        ("fe80::1%eth0", False),
        ("not an address", False),
        ("", False),
    ],
)
def test_is_public(address: str, public: bool) -> None:
    assert is_public(address) is public


async def test_a_host_that_does_not_resolve_is_a_failure_not_a_refusal() -> None:
    f, web = fetcher()
    with pytest.raises(FetchFailed, match="does not resolve"):
        await f.fetch("https://gone.example.test/")
    assert web.seen == []


# ── Failures ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [400, 403, 404, 410, 429, 500, 503])
async def test_an_error_status_is_a_failure_that_says_which(status: int) -> None:
    f, _ = fetcher(lambda _: httpx.Response(status))
    with pytest.raises(FetchFailed) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.status == status


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")],
)
async def test_a_network_failure_is_a_failure_with_the_cause_kept(failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    f, _ = fetcher(handler)
    with pytest.raises(FetchFailed) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.status is None and raised.value.__cause__ is failure


async def test_a_slow_drip_is_cut_by_the_total_timeout() -> None:
    async def slow() -> AsyncIterator[bytes]:
        for _ in range(100):
            await asyncio.sleep(0.05)
            yield b"<p>x</p>"

    f, _ = fetcher(
        lambda _: httpx.Response(200, headers={"content-type": "text/html"}, content=slow()),
        fetch_total_timeout_s=0.2,
    )
    with pytest.raises(FetchFailed, match="timed out"):
        await f.fetch(PAGE_URL)


# ── Size ─────────────────────────────────────────────────────────────────────────────────────


async def test_a_declared_length_over_the_cap_is_refused_unread() -> None:
    f, _ = fetcher(lambda _: html_response("x" * 500), fetch_max_bytes=100)
    with pytest.raises(FetchUnsupported, match="declared"):
        await f.fetch(PAGE_URL)


async def test_a_body_that_outgrows_the_cap_while_streaming_is_refused_not_truncated() -> None:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(50):
            yield b"x" * 10

    f, _ = fetcher(
        lambda _: httpx.Response(200, headers={"content-type": "text/html"}, content=body()),
        fetch_max_bytes=100,
    )
    with pytest.raises(FetchUnsupported, match="exceeds the cap"):
        await f.fetch(PAGE_URL)


async def test_the_cap_counts_the_decoded_body_so_a_compression_bomb_stops_at_it() -> None:
    bomb = gzip.compress(b"a" * 1_000_000)  # a few hundred bytes on the wire
    assert len(bomb) < 2_000
    f, _ = fetcher(
        lambda _: httpx.Response(
            200, content=bomb, headers={"content-type": "text/html", "content-encoding": "gzip"}
        ),
        fetch_max_bytes=10_000,
    )
    with pytest.raises(FetchUnsupported, match="exceeds the cap"):
        await f.fetch(PAGE_URL)


async def test_a_body_exactly_at_the_cap_is_taken() -> None:
    f, _ = fetcher(lambda _: html_response("x" * 100), fetch_max_bytes=100)
    assert len((await f.fetch(PAGE_URL)).html) == 100


# ── The typed errors ─────────────────────────────────────────────────────────────────────────


def test_every_fetch_error_is_a_fetch_error() -> None:
    assert all(issubclass(e, FetchError) for e in (FetchRefused, FetchFailed, FetchUnsupported))
    assert FetchFailed("x").status is None


def test_the_fetch_limits_are_configuration_not_constants() -> None:
    assert CONFIG.fetch_timeout_s > 0 and CONFIG.fetch_total_timeout_s >= CONFIG.fetch_timeout_s
    assert CONFIG.fetch_max_bytes >= 1 and CONFIG.fetch_max_redirects >= 0
    assert CONFIG.page_cache_ttl_s >= 1 and CONFIG.fetch_user_agent
