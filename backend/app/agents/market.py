"""A2, market and advertisement intelligence (Phases.md Phase 6 chunk B; prompt `a2_market@v1`).

Phase 6's A2 is LIVE COMPETITOR RESEARCH through the research layer (Phases.md 6.1); the
persistent advertisement repository with change tracking is Phase 9. It reads the pages step 6
accepted and reports category conventions, competitors' messages and whitespace.

The pages are third-party text, so this agent is where the project first hands a model prose it
did not write and cannot trust:

- **The pages are data.** Each excerpt sits inside `<page id="sN">`, tag-like text inside it is
  neutralised so a page cannot close its own wrapper, and the prompt says anything inside is data,
  never instruction. That reduces the risk; it does not remove it, so the rest below does not rely
  on it.
- **The output cannot carry an injected instruction anywhere that acts.** It is a closed schema of
  short strings, nothing in it is executed or fetched, and `MarketOutput` is not shown to A6 (a
  later decision, not this one). It is context for the strategy view.
- **What can be checked is checked.** A convention or a message must quote the excerpt the model
  was shown, character for character after whitespace is collapsed; one that does not is dropped
  and counted (`dropped_unverifiable`), because an invented quotation shown to a person reads as
  evidence. The source URL is set from the page id by code, never written by the model. Whitespace
  is an inference and is labelled as one: it names its basis pages (ids that were not shown are
  dropped).
- **It says nothing about the user's own product** (Rules.md T2): it is not shown the claims, and
  a page from the product's own domain (`first_party`) is not a competitor and is skipped.

Bounded: at most six pages, 2,500 characters of each. A page that cannot be read is skipped; no
page, no model call. Standard tier. A budget refusal propagates and ends the run partial."""

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentContext
from app.agents.outputs import MarketItem, MarketOutput, Whitespace
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptLibrary
from app.llm.registry import Tier

A2_MARKET = ("a2_market", "v1")
MAX_PAGES = 6
MAX_PAGE_CHARS = 2500
MIN_QUOTE_CHARS = 12
_TAGLIKE = re.compile(r"</?\s*page\b", re.IGNORECASE)


class _RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    source: str
    quote: str = Field(min_length=1, max_length=300)


class _RawWhitespace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    basis: list[str] = Field(min_length=1, max_length=6)


class _RawMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_conventions: list[_RawItem] = Field(default_factory=list, max_length=8)
    competitor_messages: list[_RawItem] = Field(default_factory=list, max_length=10)
    whitespace: list[_RawWhitespace] = Field(default_factory=list, max_length=6)


def excerpt(text: str) -> str:
    """What the model is shown of a page: whitespace collapsed, wrapper-like text neutralised,
    capped. The quote check runs against exactly this."""
    return _TAGLIKE.sub("[page", " ".join(text.split()))[:MAX_PAGE_CHARS]


def pages_block(pages: Mapping[str, str]) -> str:
    return "\n".join(f'<page id="{pid}">\n{text}\n</page>' for pid, text in pages.items())


class MarketAnalyst:
    id = "A2"

    def __init__(self, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._gateway, self._prompts = gateway, prompts

    async def read_pages(
        self, findings: list[Mapping[str, Any]], ctx: AgentContext
    ) -> tuple[dict[str, str], dict[str, str]]:
        """(id -> excerpt, id -> url) for the best readable competitor pages."""
        texts: dict[str, str] = {}
        urls: dict[str, str] = {}
        if ctx.evidence is None:
            return texts, urls
        for f in findings:
            if len(texts) >= MAX_PAGES:
                break
            url = f.get("url")
            if not url or f.get("first_party") is True:
                continue
            body = await ctx.evidence.text(str(url))
            shown = excerpt(body) if body else ""
            if shown:
                pid = f"s{len(texts) + 1}"
                texts[pid], urls[pid] = shown, str(url)
        return texts, urls

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> MarketOutput:
        texts, urls = await self.read_pages(list(state.get("findings", [])), ctx)
        if not texts:
            return MarketOutput()
        product = state.get("product") or {}
        truth = state.get("truth") or {}
        raw = await self._gateway.structured(
            _RawMarket,
            tier=Tier.STANDARD,
            prompt=self._prompts.get(*A2_MARKET).render(
                category=product.get("category") or "(not given)",
                sector=(truth.get("sector") or "(not given)").replace("_", " "),
                brief=state.get("brief") or "(none given)",
                pages=pages_block(texts),
            ),
            ctx=ctx.call_context(self.id),
            route_reason="A2 market read: default tier",
            max_output_tokens=2048,
        )
        dropped = 0

        def verified(items: list[_RawItem]) -> list[MarketItem]:
            nonlocal dropped
            kept: list[MarketItem] = []
            for i in items:
                quote = " ".join(i.quote.split())
                if i.source in texts and len(quote) >= MIN_QUOTE_CHARS and quote in texts[i.source]:
                    kept.append(MarketItem(text=i.text, source_url=urls[i.source], quote=quote))
                else:
                    dropped += 1
            return kept

        whitespace: list[Whitespace] = []
        for w in raw.whitespace:
            basis = [urls[b] for b in dict.fromkeys(w.basis) if b in urls]
            if basis:
                whitespace.append(Whitespace(text=w.text, basis_urls=basis))
            else:
                dropped += 1
        return MarketOutput(
            category_conventions=verified(raw.category_conventions),
            competitor_messages=verified(raw.competitor_messages),
            whitespace=whitespace,
            pages_read=len(texts),
            dropped_unverifiable=dropped,
        )
