"""The claim classifier (app/core/claim_classifier.py; Phases.md Phase 3 chunk C). Mocked model:
no test calls a real one (Rules.md §6). The scripted replies stand for what a model returns; the
gates, not the model, decide every label.

Sample outputs are built on real product material from evaluation/test_set.yaml (pseudonymised,
evaluation-plan.md D4): Tidewell Loop N2 (consumer electronics) and Verdano Whey (health
supplements, regulated — the illustrative rule set).
"""

import json
from datetime import date

import pytest

from app.core.claim_classifier import (
    CLAIM_EXTRACT,
    check_copy,
    classifier_tier,
    truth_context,
)
from app.core.gates import GateReport, load_superlatives
from app.core.sector_registry import Sector, SectorConstraint
from app.core.truth import Claim, ProductTruth
from app.llm.gateway import LLMGateway
from app.llm.registry import Tier
from tests.llm_fakes import FakeCompletion, FakeLedger, ctx, make_registry, prompt_library, reply

pytestmark = pytest.mark.anyio

TODAY = date(2026, 10, 18)
LEXICON = load_superlatives()
ALWAYS = "Illustrative disclaimer (synthetic rule): consult a qualified professional before use."
PROTEIN = "Illustrative disclaimer (synthetic rule): protein content varies by serving size."

BOAT = "https://www.boat-lifestyle.com/products/boat-rockerz-255-pro-ss-edition"
LOOP = ProductTruth(
    Sector("consumer-electronics", regulated=False),
    (
        Claim("ipx7", "IPX7 rating", "verified", BOAT, False),
        Claim("drivers", "10 mm drivers", "verified", BOAT, False),
        Claim("60h", "Up to 60 hours playback (at 60% volume)", "verified", BOAT, False),
        Claim(
            "asap",
            "ASAP Charge: 20 hours playback from 10 minutes of charge",
            "verified",
            BOAT,
            False,
        ),
        Claim("sound", "unbeatable Signature Sound", "restricted", None, False),
        Claim("old", "8 hours playback", "verified", BOAT, True),  # retired
    ),
    (),
)

WN = "https://wellbeingnutrition.com/products/whey-protein-blend-swiss-chocolate"
PROTEIN_FACTS = (
    "24 g protein, 11.5 g essential amino acids, 5.4 g BCAAs, 1 g glutamine (per serving)"
)
FIRST = "India's first isolate + concentrate with Velositol"
SINCE = date(2026, 10, 17)
HS = "health-supplements"
WHEY = ProductTruth(
    Sector(HS, regulated=True),
    (
        Claim("protein", PROTEIN_FACTS, "verified", WN, False),
        Claim("velositol", "Contains Velositol", "verified", WN, False),
        Claim("first", FIRST, "restricted", None, False),
        Claim("bloating", "reduced bloating", "restricted", None, False),
    ),
    (
        SectorConstraint("p1", HS, "prohibited_phrase", SINCE, None, "phrase", "miracle cure"),
        SectorConstraint("d1", HS, "required_disclaimer", SINCE, None, "phrase", None, ALWAYS),
        SectorConstraint(
            "d2", HS, "required_disclaimer", SINCE, None, "phrase", "protein", PROTEIN
        ),
    ),
)


def model_says(*statements: tuple[str, str | None]) -> object:
    body = {
        "statements": [
            {"quote": q, "claim_id": c, "labelled_deduction": False} for q, c in statements
        ]
    }
    return reply(json.dumps(body))


def gateway(completion: FakeCompletion, *, strong: bool = True) -> tuple[LLMGateway, FakeLedger]:
    ledger = FakeLedger()
    gw = LLMGateway(
        make_registry(unique=False),
        ledger,  # type: ignore[arg-type]
        prompt_library(),
        api_key="test-key",
        timeout_s=30,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        allow_strong_tier=strong,
        completion=completion,
    )
    return gw, ledger


async def check(
    copy: dict[str, str], truth: ProductTruth, *replies: object, strong: bool = True
) -> tuple[GateReport, FakeCompletion, FakeLedger]:
    completion = FakeCompletion(*replies)
    gw, ledger = gateway(completion, strong=strong)
    report = await check_copy(gw, prompt_library(), ctx(), copy, truth, LEXICON, TODAY)
    return report, completion, ledger


# ── Exit criterion 3: every claim in a sample output resolves to a source ────────────────────

LOOP_COPY = {
    "headline": "Tidewell Loop N2: built for the monsoon",
    "body": (
        "IPX7 rating keeps the rain out. 10 mm drivers bring every word through. "
        "Up to 60 hours playback (at 60% volume), and "
        "ASAP Charge: 20 hours playback from 10 minutes of charge."
    ),
}
WHEY_COPY = {
    "body": f"{PROTEIN_FACTS}. Contains Velositol.",
    "footer": f"{ALWAYS}\n{PROTEIN}",
}


@pytest.mark.parametrize(
    ("copy", "truth", "said"),
    [
        (
            LOOP_COPY,
            LOOP,
            (
                ("IPX7 rating", "ipx7"),
                ("10 mm drivers", "drivers"),
                ("Up to 60 hours playback (at 60% volume)", "60h"),
                ("ASAP Charge: 20 hours playback from 10 minutes of charge", "asap"),
            ),
        ),
        (
            WHEY_COPY,
            WHEY,
            (
                (PROTEIN_FACTS, "protein"),
                ("Contains Velositol", "velositol"),
            ),
        ),
    ],
    ids=["tidewell-loop-n2", "verdano-whey-regulated"],
)  # fmt: skip
async def test_criterion_3_every_claim_in_a_sample_output_resolves_to_a_source(
    copy: dict[str, str], truth: ProductTruth, said: tuple[tuple[str, str], ...]
) -> None:
    report, _, _ = await check(copy, truth, model_says(*said))
    assert report.outcome == "pass", report.findings
    assert len(report.resolutions) == len(said)  # every statement resolved, none left over
    assert all(r.label == "verified" and r.evidence_url for r in report.resolutions)
    assert {r.claim_id for r in report.resolutions} == {c for _, c in said}


async def test_a_statement_no_listed_claim_supports_blocks_the_sample() -> None:
    copy = {"body": LOOP_COPY["body"] + " Lasts five years."}
    report, _, _ = await check(
        copy, LOOP, model_says(("IPX7 rating", "ipx7"), ("Lasts five years.", None))
    )
    assert [f.kind for f in report.findings] == ["unsourced_claim"]


# ── Exit criterion 2: an attempt to generate a restricted claim is blocked ───────────────────


async def test_criterion_2_a_restricted_claim_in_output_is_blocked_whatever_the_model_cites() -> (
    None
):
    # The dangerous case: the model cites a verified claim for the restricted statement. The
    # risk gate scans the copy itself, so the citation cannot launder it.
    copy = {"body": f"{FIRST}.", "footer": ALWAYS}
    report, _, _ = await check(copy, WHEY, model_says((FIRST, "velositol")))
    kinds = {f.kind for f in report.findings}
    assert "restricted_claim_in_copy" in kinds
    assert "unsupported_superlative" in kinds  # "india's first", block tier
    assert report.outcome == "block"


async def test_a_restricted_claim_the_model_ignores_is_still_blocked() -> None:
    copy = {"body": "Fewer cramps and reduced bloating.", "footer": ALWAYS}
    report, _, _ = await check(copy, WHEY, model_says())
    assert [f.kind for f in report.findings] == ["restricted_claim_in_copy"]


# ── What the model sees: never a restricted or retired claim (Rules.md T2) ───────────────────


def test_the_context_a_model_may_see_holds_verified_and_deduced_only() -> None:
    assert {c.id for c in truth_context(LOOP)} == {"ipx7", "drivers", "60h", "asap"}
    assert {c.id for c in truth_context(WHEY)} == {"protein", "velositol"}


async def test_no_restricted_claim_reaches_the_prompt() -> None:
    _, completion, _ = await check(LOOP_COPY, LOOP, model_says())
    sent = json.dumps(completion.calls[0]["messages"])
    assert "IPX7 rating" in sent and "ipx7" in sent
    assert "unbeatable Signature Sound" not in sent and "sound |" not in sent
    assert "8 hours playback" not in sent  # retired


# ── Tier (Rules.md T4) and failing closed ────────────────────────────────────────────────────


def test_regulated_output_is_classified_on_the_strong_tier() -> None:
    assert classifier_tier(LOOP)[0] is Tier.STANDARD
    tier, reason = classifier_tier(WHEY)
    assert tier is Tier.STRONG
    assert reason.startswith("T4: claim verification on regulated-sector output")
    assert "illustrative rules" in reason


async def test_the_call_is_recorded_with_its_prompt_tier_and_reason() -> None:
    _, _, ledger = await check(WHEY_COPY, WHEY, model_says())
    (row,) = ledger.rows.values()
    assert (row["prompt"].prompt_id, row["prompt"].prompt_version) == CLAIM_EXTRACT
    assert row["tier"] == "strong" and row["route_reason"].startswith("T4:")


async def test_with_the_strong_tier_off_regulated_copy_is_blocked_not_downgraded() -> None:
    report, completion, _ = await check(WHEY_COPY, WHEY, strong=False)
    assert [f.kind for f in report.findings] == ["gate_error"]
    assert "StrongTierDisabled" in report.findings[0].message
    assert completion.calls == []  # no call was made at any tier


async def test_output_that_never_validates_blocks_the_copy() -> None:
    report, completion, _ = await check(LOOP_COPY, LOOP, reply("not json"), reply("still not"))
    assert [f.kind for f in report.findings] == ["gate_error"]
    assert len(completion.calls) == 2  # one repair, then stop (Rules.md C8)


async def test_an_invented_quote_or_claim_id_is_blocked() -> None:
    report, _, _ = await check(
        LOOP_COPY, LOOP, model_says(("IPX8 rating", "ipx7"), ("10 mm drivers", "made-up"))
    )
    assert [f.kind for f in report.findings] == ["statement_not_in_copy", "cites_unknown_claim"]
