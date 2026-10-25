"""Page-content extraction (Phases.md Phase 5): main text out of HTML, boilerplate left behind.

`Extractor` turns a raw extraction (title, author, date, site name, language, text) into an
`ExtractedPage` and decides what is not a page: main text under `min_chars` is `ExtractionFailed`
(a login wall, a listing, boilerplate only), main text over `max_chars` is cut at a paragraph
boundary and flagged `truncated`. Both limits are config (`research.extraction_*` in
config/scoring.yaml), not constants here.

The library is behind a function: `trafilatura_extract` is the real binding, and it imports
trafilatura only when called. trafilatura is in the optional `extraction` dependency group
(approved by the project owner 2026-10-25; it brings lxml, a compiled wheel); CI never installs it.
CI runs `Extractor` over the library's recorded outputs for saved HTML pages
(tests/fixtures/extraction, recorded by scripts/record_extraction_fixtures.py), and the same
assertions run against the live library wherever it is installed.

Language: the library's `language` came back empty on all five fixture pages. It is carried through
but unverified, and nothing may depend on language detection until it has been shown to work on real
fetched pages.

Zero-retention (Architecture.md §6): an `ExtractedPage` holds the page's text and lives in memory or
in Redis under a TTL. Nothing here writes to Postgres; only extracted, scored, attributed findings
persist, and those are built from this by later pieces.
"""

from collections.abc import Callable, Mapping
from datetime import date
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from app.errors import ExtractionFailed

# (html, url) -> the library's fields, or None if it found no content.
RawExtract = Callable[[str, str], Mapping[str, object] | None]


class ExtractedPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str | None
    text: str
    published: date | None  # the page's own date, for freshness decay; None when it states none
    author: str | None
    sitename: str | None  # only a publisher the page states; see `_stated_sitename`
    language: str | None  # UNVERIFIED: empty on all five fixtures; nothing depends on it
    truncated: bool = False


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _date(value: object) -> date | None:
    """The library reports an ISO date (`YYYY-MM-DD`); anything else is no date, never a guess."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _stated_sitename(value: object, url: str) -> str | None:
    """The publisher the page states, or None. trafilatura fills `sitename` with the hostname when
    the page names no publisher, which would turn an unstated publisher into an apparently stated
    one, and the library does not say which it did. So a value that is the URL's host, or a parent
    domain of it (case and a leading `www.` ignored), is treated as NOT STATED. A page that really
    names itself by its domain is lost with it: the rule errs toward not stated. Every consumer
    inherits this; source scoring must not re-derive it."""
    name = _text(value)
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    if name is None:
        return None
    bare = name.casefold().removeprefix("www.")
    if host and (bare == host or host.endswith("." + bare)):
        return None
    return name


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, ending at a paragraph boundary when one exists in the second
    half of the allowance (otherwise at the limit)."""
    head = text[:limit]
    boundary = head.rfind("\n")
    return (head[:boundary] if boundary >= limit // 2 else head).rstrip()


class Extractor:
    def __init__(self, extract: RawExtract, *, min_chars: int, max_chars: int) -> None:
        if not 1 <= min_chars < max_chars:
            raise ValueError("need 1 <= min_chars < max_chars")
        self._extract, self._min_chars, self._max_chars = extract, min_chars, max_chars

    def extract(self, html: str, url: str) -> ExtractedPage:
        raw = self._extract(html, url)
        text = _text(raw.get("text")) if raw is not None else None
        if raw is None or text is None or len(text) < self._min_chars:
            raise ExtractionFailed(f"no main content at {url}")
        truncated = len(text) > self._max_chars
        return ExtractedPage(
            url=url,
            title=_text(raw.get("title")),
            text=_cut(text, self._max_chars) if truncated else text,
            published=_date(raw.get("date")),
            author=_text(raw.get("author")),
            sitename=_stated_sitename(raw.get("sitename"), url),
            language=_text(raw.get("language")),
            truncated=truncated,
        )


def trafilatura_extract(html: str, url: str) -> dict[str, object] | None:
    """The real binding. Tables are kept (a competitor's spec table is evidence); comments are
    not (reader chatter is not the page's claim); precision is favoured over recall."""
    from trafilatura import bare_extraction  # optional group: imported only when used

    document = bare_extraction(
        html,
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if document is None:
        return None
    fields = document if isinstance(document, dict) else document.as_dict()
    return {
        key: fields.get(key) for key in ("title", "author", "date", "sitename", "language", "text")
    }
