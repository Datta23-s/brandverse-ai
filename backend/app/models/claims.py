"""Product claims: what the API accepts and returns (Architecture.md §10, `/products/{id}/claims`).

The rules here mirror migration 0017's CHECKs so a caller gets a clear 422 before the database is
asked; the database stays the boundary, because members can also write product_claims through the
REST API directly. The caller never supplies who entered or retired a claim: the request models
have no such field (unknown fields are refused) and the 0017 trigger derives both from the
authenticated identity.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.models.base import Contract

ClaimType = Literal["verified", "deduced", "restricted"]

ClaimText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
EvidenceUrl = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2048, pattern=r"^https?://\S+$")
]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ProductClaimCreate(Contract):
    """A claim a signed-in owner or editor enters for a product (Phases.md Phase 3).

    verified   — needs an evidence URL (the source a reader can check).
    deduced    — needs a confidence and a basis: an evidence URL or a note.
    restricted — needs the reason it may not be claimed, in evidence_note.
    """

    org_id: UUID
    claim: ClaimText
    claim_type: ClaimType
    evidence_url: EvidenceUrl | None = None
    evidence_note: Note | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _type_rules(self) -> Self:
        if self.claim_type == "verified" and self.evidence_url is None:
            raise ValueError("a verified claim needs an evidence URL")
        if self.claim_type == "deduced":
            if self.confidence is None:
                raise ValueError("a deduced claim needs a confidence")
            if self.evidence_url is None and self.evidence_note is None:
                raise ValueError("a deduced claim needs a basis: an evidence URL or a note")
        if self.claim_type == "restricted" and self.evidence_note is None:
            raise ValueError("a restricted claim needs the reason, in evidence_note")
        return self


class ClaimRetire(Contract):
    """Retire a claim. Claims are never edited or deleted (migration 0017)."""

    org_id: UUID
    reason: Reason


class ProductClaim(Contract):
    id: UUID
    org_id: UUID
    product_id: UUID
    claim: str
    claim_type: ClaimType
    evidence_url: str | None
    evidence_note: str | None
    confidence: float | None
    # 'human' for a claim a person entered (then entered_by is that person), else an agent id.
    created_by: str
    entered_by: UUID | None
    created_at: AwareDatetime
    retired_at: AwareDatetime | None
    retired_by: UUID | None
    retired_reason: str | None
