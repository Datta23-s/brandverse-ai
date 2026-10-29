"""Shared fakes for the snapshot tests: a plan, a live-ish search and page source, an extractor."""

import asyncio
import uuid
from datetime import date

from app.errors import FetchError
from app.memory.scoring import load_scoring
from app.research.extraction import Extractor
from app.research.fetch import FetchedPage
from app.research.planner import PlanRequest, QueryPlan, plan_queries
from app.research.search import SearchResponse, SearchResult

CFG = load_scoring()
TODAY = date(2026, 10, 28)
DECLARED = ("duckduckgo web", "yahoo", "yandex", "yep")


def fresh_sentinel() -> str:
    """A string no source file contains (it is made at run time), so a scan for it finds only
    bodies that were actually written."""
    return "RAWBODY-" + uuid.uuid4().hex


def body_with(sentinel: str) -> str:
    return (
        f"neckband earphones buying guide {sentinel} battery life and sound quality compared " * 2
    )


GOOD = body_with("plain-page")


def request(category: str = "neckband earphones") -> PlanRequest:
    return PlanRequest(
        sector="consumer electronics", regulated=False, category=category, audience="Students"
    )


def plan_for(category: str = "neckband earphones") -> QueryPlan:
    return plan_queries(
        request(category), queries_per_facet=2, locale="India", max_pages_per_query=100
    )


def raw_extract(html: str, url: str) -> dict[str, object] | None:
    title = "Neckband earphones guide" if "neckband" in html else "Unrelated"
    return {"title": title, "text": html, "date": None}


EXTRACTOR = Extractor(raw_extract, min_chars=20, max_chars=2000)


def urls_for(plan: QueryPlan, per_query: int = 2) -> dict[int, list[str]]:
    return {
        q.rank: [f"https://r{q.rank}-{i}.example/p" for i in range(per_query)] for q in plan.queries
    }


class LiveSearch:
    """Answers each planned query with its URLs, counts calls, and can raise per rank."""

    def __init__(
        self,
        plan: QueryPlan,
        urls: dict[int, list[str]],
        raise_on: dict[int, Exception] | None = None,
    ) -> None:
        self.by_text = {q.text: q.rank for q in plan.queries}
        self.urls, self.raise_on, self.calls = urls, raise_on or {}, []

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        rank = self.by_text[query]
        self.calls.append(rank)
        if rank in self.raise_on:
            raise self.raise_on[rank]
        return SearchResponse(
            [
                SearchResult(url=u, title="t", snippet="s", engine="yep", engines=DECLARED)
                for u in self.urls.get(rank, [])
            ][:limit],
            declared_engines=DECLARED,
        )


class LivePages:
    """Serves bodies, fails what it is told to (a list is consumed one failure per attempt)."""

    def __init__(
        self,
        bodies: dict[str, str] | None = None,
        failing: dict[str, FetchError | list[FetchError]] | None = None,
    ) -> None:
        self.bodies, self.failing, self.calls = bodies or {}, failing or {}, []

    async def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        await asyncio.sleep(0)
        fail = self.failing.get(url)
        if isinstance(fail, list):
            if fail:
                raise fail.pop(0)
        elif fail is not None:
            raise fail
        return FetchedPage(url, url, 200, "text/html", self.bodies.get(url, GOOD), 1000.0, 0)


async def no_sleep(_seconds: float) -> None:
    return None
