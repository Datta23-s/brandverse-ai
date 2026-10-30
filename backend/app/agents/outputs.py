"""Output shapes shared by the real agents and their mocks (Architecture.md section 7)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreativeOutput(BaseModel):
    """What A6 returns: the copy, by part (headline, body, call to action, footer). NOT the
    statements in it and NOT which claims it rests on: the claim classifier extracts those from
    the copy at step 11, so a writer cannot mark its own homework
    (app/core/claim_classifier.py)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    copy_: dict[str, str] = Field(alias="copy")


class Persona(BaseModel):
    """A working hypothesis about who the product is for. `summary` is one line and is what the
    local encoder turns into the persona vector (Memory.md section 3.1, chunk C)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    summary: str = Field(min_length=1, max_length=240)
    jobs_to_be_done: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class CustomerOutput(BaseModel):
    personas: list[Persona] = Field(min_length=1, max_length=3)


AntiPattern = Literal[
    "false_scarcity",
    "fabricated_social_proof",
    "undisclosed_paid_endorsement",
    "vulnerability_targeting",
    "dark_pattern_consent",
    "unsubstantiated_health_claim",
    "price_anchoring",
    "confirmshaming",
]


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AntiPattern
    quote: str
    note: str = Field(max_length=300)


class RiskAdvisory(BaseModel):
    """A7's risk read: a MODEL OPINION for the human approver. It is not a gate result, never
    blocks, and is stored apart from the deterministic findings (`validation["advisory"]`).
    `dropped_unverifiable` counts flags whose quote was not in the copy: an invented quote is not
    shown to the approver as evidence."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["model_opinion"] = "model_opinion"
    advisory_only: Literal[True] = True
    flags: list[RiskFlag] = Field(default_factory=list)
    dropped_unverifiable: int = 0


class Differentiator(BaseModel):
    """One way the product differs, tied to the claims that support it. `claim_ids` are checked
    against the claims the model was shown; a differentiator resting on none is dropped."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=160)
    claim_ids: list[str] = Field(min_length=1)


class ProductBrandOutput(BaseModel):
    """What A1 returns. `claim_inventory` is never the model's: it is the product's active verified
    claim ids, taken from the truth by code."""

    model_config = ConfigDict(extra="forbid")

    positioning: str = Field(default="", max_length=200)
    differentiators: list[Differentiator] = Field(default_factory=list)
    claim_inventory: list[str] = Field(default_factory=list)
    dropped_unattributed: int = 0


class MarketItem(BaseModel):
    """Something found on a page: a category convention or a competitor's message. `source_url` is
    set by code from the page the model named, never written by the model; `quote` was found in
    the text the model was shown, or the item was dropped."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    source_url: str
    quote: str = Field(min_length=1, max_length=300)


class Whitespace(BaseModel):
    """A gap the pages suggest: an INFERENCE, so it carries the pages it rests on, not a quote."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    basis_urls: list[str] = Field(min_length=1)


class MarketOutput(BaseModel):
    """What A2 returns, from third-party pages read through the research layer. It is context for
    strategy about OTHER people's claims: it is never a source for a claim about the product
    (Rules.md T2), and nothing here is shown to A6."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["third_party_pages"] = "third_party_pages"
    category_conventions: list[MarketItem] = Field(default_factory=list)
    competitor_messages: list[MarketItem] = Field(default_factory=list)
    whitespace: list[Whitespace] = Field(default_factory=list)
    pages_read: int = 0
    dropped_unverifiable: int = 0
    # The ONLY part of this output A6 may be shown: short summaries (conventions and whitespace)
    # that code checked for markup, URLs and words copied from a page. Never a quote.
    for_copy: list[str] = Field(default_factory=list)
    dropped_unsafe: int = 0
