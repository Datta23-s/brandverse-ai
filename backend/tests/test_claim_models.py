"""Claim requests mirror migration 0017's CHECKs (app/models/claims.py)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.claims import ProductClaimCreate

ORG = str(uuid4())
URL = "https://example.invalid/spec"


def make(**fields: object) -> ProductClaimCreate:
    return ProductClaimCreate.model_validate({"org_id": ORG, "claim": "Weighs 240 g", **fields})


def test_a_verified_claim_with_evidence_is_accepted_and_trimmed() -> None:
    claim = make(claim="  Weighs 240 g  ", claim_type="verified", evidence_url=f" {URL} ")
    assert (claim.claim, claim.evidence_url) == ("Weighs 240 g", URL)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"claim_type": "verified"}, "needs an evidence URL"),
        ({"claim_type": "verified", "evidence_url": "example.com"}, "should match pattern"),
        ({"claim_type": "deduced", "evidence_note": "n"}, "needs a confidence"),
        ({"claim_type": "deduced", "confidence": 0.5}, "needs a basis"),
        ({"claim_type": "deduced", "confidence": 1.5, "evidence_note": "n"}, "less than or equal"),
        ({"claim_type": "restricted"}, "needs the reason"),
        ({"claim_type": "proven", "evidence_url": URL}, "verified', 'deduced' or 'restricted'"),
        ({"claim_type": "verified", "evidence_url": URL, "claim": "   "}, "at least 1 character"),
    ],
)
def test_each_type_rule_is_enforced(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make(**fields)


@pytest.mark.parametrize("field", ["created_by", "entered_by", "retired_by", "retired_at"])
def test_the_caller_cannot_name_who_entered_or_retired_a_claim(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make(claim_type="verified", evidence_url=URL, **{field: "human"})
