"""Page-content extraction (app/research/extraction.py; Phases.md Phase 5).

CI does not install trafilatura (optional `extraction` group). It runs `Extractor` over the
library's recorded output for each saved HTML page in tests/fixtures/extraction; wherever the
library is installed, the same assertions run against the live library, and its output must equal
the recording. Recording: `uv run python -m scripts.record_extraction_fixtures`.
"""

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

import pytest

from app.errors import ExtractionFailed
from app.memory.scoring import load_scoring
from app.research.extraction import ExtractedPage, Extractor, RawExtract, trafilatura_extract

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
PAGES = sorted(p.stem for p in FIXTURES.glob("*.html"))
CONFIG = load_scoring().research


def html_of(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def recording_of(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))


def recorded(html: str, url: str) -> Mapping[str, object] | None:
    """The library's recorded output for this page; refuses a page changed since it was recorded."""
    name = Path(url).stem
    expected = recording_of(name)
    assert expected["html_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest(), (
        f"{name}.html changed since it was recorded; run scripts.record_extraction_fixtures"
    )
    raw = expected["raw"]
    assert raw is None or isinstance(raw, dict)
    return raw


def extractor(raw: RawExtract) -> Extractor:
    return Extractor(
        raw, min_chars=CONFIG.extraction_min_chars, max_chars=CONFIG.extraction_max_chars
    )


def url_of(name: str) -> str:
    return str(recording_of(name)["url"])


# What each page must yield: boilerplate and comments gone, the real content and metadata kept.
# The same function checks the recording in CI and the live library where it is installed.


def check_page(name: str, run: Callable[[str, str], ExtractedPage]) -> None:
    html, url = html_of(name), url_of(name)
    if name == "login_wall":
        with pytest.raises(ExtractionFailed, match="no main content"):
            run(html, url)
        return
    page = run(html, url)
    wanted, unwanted, published = {
        "article_with_boilerplate": (
            ["polymerised oil", "When to re-season", "worn thin"],
            ["Log in", "Basket", "cookies", "Related stories", "prize draw", "Copyright"],
            date(2026, 3, 14),
        ),
        "article_no_date": (
            ["starter set removes that guesswork", "induction hob"],
            ["Contact us", "Careers", "Sitemap"],
            None,
        ),
        "article_with_comments": (
            ["firmness is the property", "one hundred nights"],
            ["sam_k", "discount code", "Subscribe", "14 comments", "Advertise"],
            date(2026, 2, 2),
        ),
        "product_specs_table": (
            ["Lug depth", "292 g", "Two years against manufacturing defects", "not waterproof"],
            ["Free delivery", "Sign up", "Returns"],
            date(2026, 1, 20),
        ),
    }[name]
    for phrase in wanted:
        assert phrase in page.text, f"{name}: lost {phrase!r}"
    for phrase in unwanted:
        assert phrase not in page.text, f"{name}: kept boilerplate {phrase!r}"
    assert page.published == published
    assert page.url == url and not page.truncated


def test_every_saved_page_has_a_recording_made_by_the_pinned_library() -> None:
    pin = re.search(r'"trafilatura==([^"]+)"', PYPROJECT.read_text(encoding="utf-8"))
    assert pin is not None
    assert PAGES, "no fixtures"
    for name in PAGES:
        recording = recording_of(name)  # a missing recording is a failure here
        assert recording["trafilatura"] == pin.group(1), f"{name}: re-record after a version bump"
        recorded(html_of(name), url_of(name))  # and the page is the one that was recorded


@pytest.mark.parametrize("name", PAGES)
def test_extractor_over_the_recorded_library_output(name: str) -> None:
    check_page(name, extractor(recorded).extract)


@pytest.mark.parametrize("name", PAGES)
def test_the_live_library_matches_its_recording_and_the_same_assertions(name: str) -> None:
    pytest.importorskip("trafilatura")
    assert trafilatura_extract(html_of(name), url_of(name)) == recorded(html_of(name), url_of(name))
    check_page(name, extractor(trafilatura_extract).extract)


def test_the_recorded_pages_carry_metadata_the_extractor_surfaces() -> None:
    page = extractor(recorded).extract(
        html_of("article_with_boilerplate"), url_of("article_with_boilerplate")
    )
    assert (page.title, page.author, page.sitename) == (
        "How to season cast iron",
        "Asha Rao",
        "Example Cookware Journal",
    )
    assert page.text.startswith("How to season cast iron\n")


# ── The Extractor's own rules, independent of the library ────────────────────────────────────


def raw_with(**fields: object) -> RawExtract:
    def run(html: str, url: str) -> dict[str, object]:
        return {"text": "word " * 100, **fields}

    return run


def small(raw: RawExtract, *, min_chars: int = 50, max_chars: int = 1000) -> Extractor:
    return Extractor(raw, min_chars=min_chars, max_chars=max_chars)


def test_the_limits_come_from_scoring_config() -> None:
    assert 1 <= CONFIG.extraction_min_chars < CONFIG.extraction_max_chars
    with pytest.raises(ValueError, match="min_chars"):
        Extractor(raw_with(), min_chars=10, max_chars=10)
    with pytest.raises(TypeError, match="min_chars"):
        Extractor(raw_with())  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw",
    [
        lambda html, url: None,
        lambda html, url: {},
        lambda html, url: {"text": None},
        lambda html, url: {"text": 12345},
        lambda html, url: {"text": "   \n  "},
        lambda html, url: {"text": "too short"},
    ],
)
def test_a_page_without_enough_main_text_is_refused(raw: RawExtract) -> None:
    with pytest.raises(ExtractionFailed, match="https://x.test/p"):
        small(raw).extract("<html></html>", "https://x.test/p")


def test_text_at_exactly_the_minimum_is_kept() -> None:
    page = small(lambda h, u: {"text": "a" * 50}).extract("", "https://x.test/")
    assert len(page.text) == 50


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-14", date(2026, 3, 14)),
        ("2026-03-14T08:30:00+00:00", date(2026, 3, 14)),
        (" 2026-03-14 ", date(2026, 3, 14)),
        ("14 March 2026", None),  # not ISO: no date rather than a guess
        ("2026-13-45", None),
        ("", None),
        (None, None),
        (20260314, None),
    ],
)
def test_the_published_date_is_read_only_when_it_is_an_iso_date(
    value: object, expected: date | None
) -> None:
    page = small(raw_with(date=value)).extract("", "https://x.test/")
    assert page.published == expected


def test_blank_and_non_string_metadata_become_none() -> None:
    page = small(raw_with(title="  ", author=3, sitename=None, language="")).extract(
        "", "https://x.test/"
    )
    assert (page.title, page.author, page.sitename, page.language) == (None, None, None, None)


def test_overlong_text_is_cut_at_a_paragraph_boundary_and_flagged() -> None:
    paragraphs = [f"p{n} " + "x" * 37 for n in range(10)]  # 40 characters each
    text = "\n".join(paragraphs)  # 409 characters
    page = small(lambda h, u: {"text": text}, min_chars=10, max_chars=250).extract("", "https://x/")
    assert page.truncated
    assert page.text == "\n".join(paragraphs[:6])  # whole paragraphs only; 6 x 40 + 5 newlines
    assert len(page.text) <= 250


def test_overlong_text_with_no_late_boundary_is_cut_at_the_limit() -> None:
    page = small(lambda h, u: {"text": "short\n" + "y" * 500}, min_chars=10, max_chars=200).extract(
        "", "https://x/"
    )
    assert page.truncated and len(page.text) == 200


def test_text_within_the_limit_is_not_flagged() -> None:
    assert not small(raw_with(), max_chars=10_000).extract("", "https://x/").truncated


def test_a_site_name_the_library_derived_from_the_host_is_not_a_stated_publisher() -> None:
    # The recording: no publisher on the page, and the library answered with the hostname.
    recording = recording_of("article_no_date")
    raw = recording["raw"]
    assert isinstance(raw, dict) and raw["sitename"] == "fixtures.example.test"
    page = extractor(recorded).extract(html_of("article_no_date"), url_of("article_no_date"))
    assert page.sitename is None


@pytest.mark.parametrize(
    ("sitename", "url", "expected"),
    [
        ("fixtures.example.test", "https://fixtures.example.test/a", None),  # the host itself
        ("Fixtures.Example.Test", "https://fixtures.example.test/a", None),  # case
        ("example.test", "https://journal.example.test/a", None),  # a parent domain of the host
        ("www.example.test", "https://example.test/a", None),  # a leading www.
        ("example.test", "https://www.example.test/a", None),
        ("Example Cookware Journal", "https://journal.example.test/a", "Example Cookware Journal"),
        ("Example", "https://example.test/a", "Example"),  # a stated name that is not a domain
        ("other.test", "https://example.test/a", "other.test"),  # a different domain is stated
        ("ample.test", "https://example.test/a", "ample.test"),  # not a parent: no suffix trick
        (None, "https://example.test/a", None),
    ],
)
def test_the_stated_versus_derived_site_name_rule(
    sitename: str | None, url: str, expected: str | None
) -> None:
    page = small(raw_with(sitename=sitename)).extract("", url)
    assert page.sitename == expected
