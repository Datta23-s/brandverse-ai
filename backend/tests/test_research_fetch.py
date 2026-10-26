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
    """A mock web: records every page request in `seen` and every robots.txt request in
    `robots_seen`, answers pages with `handler` and robots.txt with `robots` (default: 404, which
    allows everything), so the tests about pages do not have to know robots.txt exists."""

    def __init__(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        robots: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self.handler, self.seen, self.robots_seen = handler, [], []
        self.robots = robots or (lambda _: httpx.Response(404))

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            self.robots_seen.append(request)
            return self.robots(request)
        self.seen.append(request)
        return self.handler(request)

    @property
    def hosts(self) -> list[str]:
        return [r.url.host for r in self.seen]


def robots_file(text: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(
        200, content=text.encode(), headers={"content-type": "text/plain"}
    )


async def resolver(host: str, port: int) -> list[str]:
    if host in DNS:
        return DNS[host]
    if host == "gone.example.test":
        raise OSError("name or service not known")
    return [host]  # an IP literal resolves to itself, as getaddrinfo does


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


def fetcher(
    handler: Callable[[httpx.Request], httpx.Response] = lambda _: html_response(),
    *,
    robots: Callable[[httpx.Request], httpx.Response] | None = None,
    clock: Callable[[], float] | None = None,
    **cfg: object,
) -> tuple[PageFetcher, Web]:
    web = Web(handler, robots)
    client = httpx.AsyncClient(transport=httpx.MockTransport(web))
    config: Research = CONFIG.model_copy(update=cfg)
    return PageFetcher(client, config, resolver=resolver, clock=clock or Clock()), web


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


@pytest.mark.parametrize("content_type", ["image/png", "application/json", "text/plain"])
async def test_what_is_not_html_is_unsupported_with_its_kind(content_type: str) -> None:
    f, _ = fetcher(
        lambda _: httpx.Response(200, content=b"data", headers={"content-type": content_type})
    )
    with pytest.raises(FetchUnsupported, match="not HTML") as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "not_html"


async def test_a_response_with_no_content_type_is_refused_not_sniffed() -> None:
    f, _ = fetcher(
        lambda _: httpx.Response(200, content=HTML.encode(), headers={"content-type": ""})
    )
    with pytest.raises(FetchUnsupported, match="no content type") as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "no_content_type"


# ── PDFs: skipped on purpose, visibly (owner, 2026-10-26) ────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.test/files/circular.pdf",
        "https://shop.example.test/files/CIRCULAR.PDF",
        "https://shop.example.test/files/circular.pdf?download=1",
        "https://shop.example.test/files/circular.pdf#page=3",
    ],
)
async def test_a_pdf_url_is_skipped_as_kind_pdf_without_any_request(url: str) -> None:
    f, web = fetcher()
    with pytest.raises(FetchUnsupported, match="PDF") as raised:
        await f.fetch(url)
    assert raised.value.kind == "pdf"
    assert web.seen == [] and web.robots_seen == []  # not even robots.txt was fetched for it


async def test_a_pdf_served_at_a_url_that_does_not_say_so_is_kind_pdf_too() -> None:
    f, _ = fetcher(
        lambda _: httpx.Response(
            200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
        )
    )
    with pytest.raises(FetchUnsupported, match="PDF") as raised:
        await f.fetch("https://shop.example.test/download?id=7")
    assert raised.value.kind == "pdf"


async def test_a_page_whose_name_merely_contains_pdf_is_not_skipped() -> None:
    f, _ = fetcher()
    assert (await f.fetch("https://shop.example.test/pdf-guide/kadai")).status == 200


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
    with pytest.raises(FetchUnsupported, match="declared") as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "too_large"


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


# ── robots.txt is honoured (owner, 2026-10-26) ───────────────────────────────────────────────

BLOCK_PRODUCTS = "User-agent: *\nDisallow: /products/\n"


async def test_a_page_robots_txt_disallows_is_refused_and_never_requested() -> None:
    f, web = fetcher(robots=robots_file(BLOCK_PRODUCTS))
    with pytest.raises(FetchRefused, match="robots.txt") as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "robots"
    assert web.seen == [] and len(web.robots_seen) == 1


async def test_a_page_it_allows_is_fetched_and_the_robots_request_says_who_asks() -> None:
    f, web = fetcher(robots=robots_file(BLOCK_PRODUCTS))
    assert (await f.fetch("https://shop.example.test/about")).status == 200
    (robots_request,) = web.robots_seen
    assert str(robots_request.url) == "https://shop.example.test/robots.txt"
    assert robots_request.headers["user-agent"] == CONFIG.fetch_user_agent


async def test_a_site_can_block_this_fetcher_by_name() -> None:
    text = "User-agent: BrandverseResearch\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
    f, web = fetcher(robots=robots_file(text))
    with pytest.raises(FetchRefused) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "robots" and web.seen == []


async def test_the_longest_rule_decides_as_the_rfc_says() -> None:
    f, _ = fetcher(robots=robots_file("User-agent: *\nDisallow: /\nAllow: /products/\n"))
    assert (await f.fetch(PAGE_URL)).status == 200


async def test_robots_txt_is_fetched_once_per_origin_not_once_per_page() -> None:
    f, web = fetcher(robots=robots_file(BLOCK_PRODUCTS))
    for path in ("/a", "/b", "/c"):
        await f.fetch("https://shop.example.test" + path)
    await f.fetch("https://other.example.test/a")
    assert len(web.seen) == 4 and f.robots_fetches == 2
    hosts = sorted(r.url.host for r in web.robots_seen)
    assert hosts == ["other.example.test", "shop.example.test"]


async def test_concurrent_fetches_of_one_origin_share_one_robots_request() -> None:
    f, web = fetcher(robots=robots_file(BLOCK_PRODUCTS))
    pages = await asyncio.gather(*(f.fetch(f"https://shop.example.test/p{n}") for n in range(5)))
    assert len(pages) == 5 and len(web.robots_seen) == 1


async def test_the_cached_file_expires_after_the_ttl_in_force() -> None:
    clock = Clock()
    f, web = fetcher(clock=clock, robots_ttl_s=100)
    await f.fetch("https://shop.example.test/a")
    clock.now += 99
    await f.fetch("https://shop.example.test/b")
    assert len(web.robots_seen) == 1
    clock.now += 2  # 101 s old
    await f.fetch("https://shop.example.test/c")
    assert len(web.robots_seen) == 2


async def test_a_changed_robots_txt_is_picked_up_after_expiry() -> None:
    clock = Clock()
    state = {"text": ""}
    f, _ = fetcher(clock=clock, robots_ttl_s=100, robots=lambda r: robots_file(state["text"])(r))
    assert (await f.fetch(PAGE_URL)).status == 200
    state["text"] = BLOCK_PRODUCTS
    assert (await f.fetch(PAGE_URL)).status == 200  # still within the TTL: the old file stands
    clock.now += 101
    with pytest.raises(FetchRefused):
        await f.fetch(PAGE_URL)


@pytest.mark.parametrize("status", [404, 410, 401, 403, 429])
async def test_a_robots_txt_that_is_unavailable_to_us_allows_everything(status: int) -> None:
    f, web = fetcher(robots=lambda _: httpx.Response(status))
    assert (await f.fetch(PAGE_URL)).status == 200 and len(web.seen) == 1  # RFC 9309 2.3.1.3


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_robots_txt_server_error_is_not_permission(status: int) -> None:
    f, web = fetcher(robots=lambda _: httpx.Response(status))
    with pytest.raises(FetchFailed) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "robots_unavailable" and raised.value.status == status
    assert web.seen == []  # the page was not requested


async def test_an_unreachable_robots_txt_is_not_permission_and_is_retried_not_cached() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    f, web = fetcher(robots=broken)
    for _ in range(2):
        with pytest.raises(FetchFailed) as raised:
            await f.fetch(PAGE_URL)
        assert raised.value.kind == "robots_unavailable"
    assert len(web.robots_seen) == 2 and web.seen == []


async def test_robots_txt_redirects_are_followed_under_the_same_address_policy() -> None:
    def robots(request: httpx.Request) -> httpx.Response:
        if request.url.host == "shop.example.test":
            to = "https://other.example.test/robots.txt"
            return httpx.Response(301, headers={"location": to})
        return robots_file(BLOCK_PRODUCTS)(request)

    f, web = fetcher(robots=robots)
    with pytest.raises(FetchRefused) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "robots" and web.seen == []


async def test_a_robots_txt_that_redirects_to_a_private_host_is_not_permission() -> None:
    to = "https://internal.example.test/robots.txt"
    f, web = fetcher(robots=lambda _: httpx.Response(302, headers={"location": to}))
    with pytest.raises(FetchFailed) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "robots_unavailable"
    assert "internal.example.test" not in [r.url.host for r in web.robots_seen]  # never contacted
    assert web.seen == []


async def test_a_robots_txt_that_redirects_forever_counts_as_unavailable_and_allows() -> None:
    f, web = fetcher(
        robots=lambda r: httpx.Response(
            302, headers={"location": "/robots.txt?" + r.url.query.decode() + "x"}
        ),
        fetch_max_redirects=2,
    )
    assert (await f.fetch(PAGE_URL)).status == 200  # RFC 9309 2.3.1.2
    assert len(web.robots_seen) == 3


async def test_only_the_first_robots_max_bytes_are_read() -> None:
    padding = "# " + "x" * 100 + "\n"
    text = "User-agent: *\nDisallow: /early/\n" + padding * 3 + "Disallow: /products/\n"
    f, _ = fetcher(robots=robots_file(text), robots_max_bytes=len(padding) + 40)
    assert (await f.fetch(PAGE_URL)).status == 200  # the late rule was beyond the cap
    with pytest.raises(FetchRefused):
        await f.fetch("https://shop.example.test/early/x")


async def test_a_redirect_target_on_another_origin_is_judged_by_its_own_robots_txt() -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        if request.url.host == "shop.example.test":
            return httpx.Response(302, headers={"location": "https://other.example.test/blocked/p"})
        return html_response()

    def robots(request: httpx.Request) -> httpx.Response:
        if request.url.host == "other.example.test":
            return robots_file("User-agent: *\nDisallow: /blocked/\n")(request)
        return httpx.Response(404)

    f, web = fetcher(pages, robots=robots)
    with pytest.raises(FetchRefused) as raised:
        await f.fetch(PAGE_URL)
    assert raised.value.kind == "robots"
    assert web.hosts == ["shop.example.test"]  # the other origin's page was never requested


async def test_the_query_string_is_part_of_what_robots_txt_is_asked_about() -> None:
    f, _ = fetcher(robots=robots_file("User-agent: *\nDisallow: /*?session=\n"))
    assert (await f.fetch(PAGE_URL)).status == 200
    with pytest.raises(FetchRefused):
        await f.fetch(PAGE_URL + "?session=abc")


async def test_refusals_by_the_address_policy_never_reach_robots_txt() -> None:
    f, web = fetcher()
    with pytest.raises(FetchRefused):
        await f.fetch("https://loopback.example.test/")
    assert web.robots_seen == [] and web.seen == []


@pytest.mark.parametrize(
    ("url", "error", "kind"),
    [
        ("ftp://shop.example.test/x", FetchRefused, "scheme"),
        ("https://u:p@shop.example.test/x", FetchRefused, "credentials"),
        ("https://shop.example.test:8443/x", FetchRefused, "port"),
        ("https://loopback.example.test/", FetchRefused, "address"),
        ("https://gone.example.test/", FetchFailed, "dns"),
    ],
)
async def test_every_refusal_and_failure_says_why(
    url: str, error: type[Exception], kind: str
) -> None:
    f, _ = fetcher()
    with pytest.raises(error) as raised:
        await f.fetch(url)
    assert raised.value.kind == kind  # type: ignore[attr-defined]


# ── content_type_of: what a URL answers with, for measuring the share of PDFs ────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("application/pdf", "application/pdf"),
        ("TEXT/HTML; charset=utf-8", "text/html"),
        ("", ""),
    ],
)
async def test_content_type_of_reads_the_media_type_from_the_headers(
    header: str, expected: str
) -> None:
    f, web = fetcher(lambda _: httpx.Response(200, content=b"x", headers={"content-type": header}))
    assert await f.content_type_of("https://shop.example.test/download?id=7") == expected
    assert len(web.seen) == 1


async def test_content_type_of_does_not_special_case_a_pdf_name() -> None:
    f, web = fetcher()  # the server says HTML, whatever the URL looks like
    assert await f.content_type_of("https://shop.example.test/page.pdf") == "text/html"
    assert len(web.seen) == 1


async def test_content_type_of_obeys_robots_and_the_address_policy_like_fetch() -> None:
    f, web = fetcher(robots=robots_file(BLOCK_PRODUCTS))
    with pytest.raises(FetchRefused) as raised:
        await f.content_type_of(PAGE_URL)
    assert raised.value.kind == "robots" and web.seen == []
    with pytest.raises(FetchRefused) as raised:
        await f.content_type_of("http://169.254.169.254/")
    assert raised.value.kind == "address"


async def test_content_type_of_reports_an_error_status_as_a_failure() -> None:
    f, _ = fetcher(lambda _: httpx.Response(404))
    with pytest.raises(FetchFailed) as raised:
        await f.content_type_of(PAGE_URL)
    assert raised.value.status == 404 and raised.value.kind == "http_status"
