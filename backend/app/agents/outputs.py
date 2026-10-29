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
