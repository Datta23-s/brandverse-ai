"""The Validation Engine's deterministic gates (app/core/gates.py; Architecture.md §8).

Pure: the product's truth and the sector's rules are built here, so each finding is asserted on
its own. The same gates against the real synthetic rule set (0021) are in tests/db/test_gates_db.py.
"""

import shutil
from datetime import date
from pathlib import Path
from typing import get_args

import pytest

from app.core import gates
from app.core.gates import (
    GATE_OF,
    SEVERITY,
    FindingKind,
    Statement,
    Superlatives,
    evaluate,
    load_superlatives,
    voice_scoring_text,
)
from app.core.sector_registry import Sector, SectorConstraint
from app.core.truth import Claim, ProductTruth

TODAY = date(2026, 10, 17)
LEXICON = Superlatives(
    "test",
    block=("best", "fastest", "india's first", "market leading"),
    flag=("leading", "the only"),
)
SUPPLEMENTS = Sector("health-supplements", regulated=True)
FOOTWEAR = Sector("footwear", regulated=False)
ALWAYS = "Illustrative disclaimer (synthetic rule): consult a qualified professional before use."
PROTEIN = "Illustrative disclaimer (synthetic rule): protein content varies by serving size."

VERIFIED = Claim(
    "v1", "Upper made of recycled mesh", "verified", "https://example.invalid/v1", False
)
FASTEST = Claim(
    "v2", "Fastest drying sole we make", "verified", "https://example.invalid/v2", False
)
DEDUCED = Claim("d1", "Suits daily commuting", "deduced", None, False)
RESTRICTED = Claim("r1", "Clinically proven to boost immunity", "restricted", None, False)
RETIRED = Claim("v9", "Weighs 240 g", "verified", "https://example.invalid/v9", True)


def _rule(
    rule_id: str,
    kind: str,
    pattern: str | None,
    match_type: str = "phrase",
    text: str | None = None,
) -> SectorConstraint:
    return SectorConstraint(
        rule_id,
        "health-supplements",
        kind,
        date(2026, 10, 17),
        None,
        match_type,
        pattern,
        text,  # type: ignore[arg-type]
    )


RULES = (
    _rule("p1", "prohibited_phrase", "miracle cure"),
    _rule("p2", "prohibited_phrase", r"\bguaranteed\s+results?\b", "regex"),
    _rule("d-always", "required_disclaimer", None, text=ALWAYS),
    _rule("d-protein", "required_disclaimer", "protein", text=PROTEIN),
)


def truth(
    sector: Sector | None = FOOTWEAR, rules: tuple[SectorConstraint, ...] = ()
) -> ProductTruth:
    return ProductTruth(sector, (VERIFIED, FASTEST, DEDUCED, RESTRICTED, RETIRED), rules)


def kinds(
    copy: dict[str, str], statements: tuple[Statement, ...] = (), t: ProductTruth | None = None
) -> list[str]:
    return [f.kind for f in evaluate(copy, statements, t or truth(), LEXICON, TODAY).findings]


# ── The severity map: closed, blocking, immutable ────────────────────────────────────────────


def test_every_finding_kind_has_an_outcome_and_a_gate() -> None:
    assert set(SEVERITY) == set(get_args(FindingKind)) == set(GATE_OF)


def test_only_a_labelled_deduction_and_a_context_superlative_flag() -> None:
    flagged = {k for k, v in SEVERITY.items() if v != "block"}
    assert flagged == {"deduced_labelled", "context_superlative"}
    assert set(SEVERITY.values()) == {"block", "flag"}  # nothing is ever only a warning


ANTI_PATTERNS = (
    "false_scarcity", "fabricated_social_proof", "undisclosed_paid_endorsement",
    "vulnerability_targeting", "dark_pattern_consent", "unsubstantiated_health_claim",
    "fictional_price_anchoring", "confirmshaming",
)  # fmt: skip


def test_no_anti_pattern_is_listed_until_a_detector_exists() -> None:
    # Rules.md T5 is unmet: no detector exists, so the outcome table must not claim enforcement.
    # A detector's finding kind is added, mapped to block, in the same change that builds it.
    listed = {
        k
        for k in SEVERITY
        if any(
            word in k
            for word in (
                "scarcity",
                "social_proof",
                "endorsement",
                "vulnerab",
                "dark_pattern",
                "health_claim",
                "anchoring",
                "shaming",
            )
        )
    }
    assert listed == set()
    assert not set(ANTI_PATTERNS) & set(SEVERITY)


def test_the_maps_cannot_be_changed_at_runtime() -> None:
    with pytest.raises(TypeError):
        SEVERITY["prohibited_phrase"] = "flag"  # type: ignore[index]


# ── Claim gate: labels come from what a statement cites ──────────────────────────────────────


def test_a_statement_citing_a_verified_claim_resolves_to_its_source() -> None:
    copy = {"body": "Its upper is made of recycled mesh."}
    report = evaluate(
        copy, (Statement("upper is made of recycled mesh", "v1"),), truth(), LEXICON, TODAY
    )
    assert report.outcome == "pass"
    assert [(r.label, r.claim_id, r.evidence_url) for r in report.resolutions] == [
        ("verified", "v1", "https://example.invalid/v1")
    ]


@pytest.mark.parametrize(
    ("statement", "kind"),
    [
        (Statement("Its upper is light.", None), "unsourced_claim"),
        (Statement("Its upper is light.", "nope"), "cites_unknown_claim"),
        (Statement("Its upper is light.", "v9"), "cites_retired_claim"),
        (Statement("Its upper is light.", "r1"), "cites_restricted_claim"),
        (Statement("Its upper is light.", "d1"), "deduced_unlabelled"),
        (Statement("A quote that is not in the copy.", "v1"), "statement_not_in_copy"),
    ],
)
def test_anything_but_a_verified_or_labelled_deduced_citation_blocks(
    statement: Statement, kind: str
) -> None:
    report = evaluate({"body": "Its upper is light."}, (statement,), truth(), LEXICON, TODAY)
    assert [f.kind for f in report.findings] == [kind]
    assert report.outcome == "block"


def test_a_labelled_deduction_is_flagged_not_blocked() -> None:
    copy = {"body": "We think it suits daily commuting."}
    report = evaluate(
        copy,
        (Statement("suits daily commuting", "d1", labelled_deduction=True),),
        truth(),
        LEXICON,
        TODAY,
    )
    assert report.outcome == "flag"
    assert report.resolutions[0].label == "deduced"


def test_a_quote_may_differ_from_the_copy_only_in_layout() -> None:
    copy = {"body": "Its upper is made of\nrecycled  mesh."}
    assert kinds(copy, (Statement("upper is made of recycled mesh", "v1"),)) == []


# ── Risk gate: restricted claims, registry, superlatives ─────────────────────────────────────


def test_a_restricted_claim_in_the_copy_blocks_with_no_statement_at_all() -> None:
    assert kinds({"headline": "Clinically proven to boost immunity!"}) == [
        "restricted_claim_in_copy"
    ]


@pytest.mark.parametrize(
    ("copy", "rule"),
    [("The miracle cure for tired mornings.", "p1"), ("Guaranteed  RESULTS in a week.", "p2")],
)
def test_a_registry_prohibited_phrasing_blocks(copy: str, rule: str) -> None:
    body = f"{copy} {ALWAYS}"
    report = evaluate({"body": body}, (), truth(SUPPLEMENTS, RULES), LEXICON, TODAY)
    assert [(f.kind, f.refs) for f in report.findings] == [("prohibited_phrase", (rule,))]
    assert "illustrative rules" in report.findings[0].message  # provenance printed


def test_regulated_output_missing_a_required_disclaimer_blocks() -> None:
    report = evaluate(
        {"body": "A daily greens blend."}, (), truth(SUPPLEMENTS, RULES), LEXICON, TODAY
    )
    assert [(f.kind, f.refs) for f in report.findings] == [("missing_disclaimer", ("d-always",))]


def test_a_triggered_disclaimer_is_required_only_when_its_trigger_appears() -> None:
    no_trigger = {"body": "A daily greens blend.", "footer": ALWAYS}
    assert kinds(no_trigger, t=truth(SUPPLEMENTS, RULES)) == []
    triggered = {"body": "24 g of protein a scoop.", "footer": ALWAYS}
    assert kinds(triggered, t=truth(SUPPLEMENTS, RULES)) == ["missing_disclaimer"]
    both = {"body": "24 g of protein a scoop.", "footer": f"{ALWAYS} {PROTEIN}"}
    assert kinds(both, t=truth(SUPPLEMENTS, RULES)) == []


def test_a_disclaimer_passes_with_a_trailing_space_or_a_line_break() -> None:
    wrapped = ALWAYS.replace(" a qualified ", " a qualified\n") + "  "
    assert kinds({"body": "Greens.", "footer": wrapped}, t=truth(SUPPLEMENTS, RULES)) == []


@pytest.mark.parametrize(
    "altered",
    [ALWAYS.lower(), ALWAYS.rstrip("."), ALWAYS.replace("synthetic rule)", "synthetic rule) -")],
    ids=["case", "punctuation", "punctuation-added"],
)
def test_a_reworded_disclaimer_is_altered_not_missing(altered: str) -> None:
    report = evaluate(
        {"body": "Greens.", "footer": altered}, (), truth(SUPPLEMENTS, RULES), LEXICON, TODAY
    )
    assert [(f.kind, f.gate) for f in report.findings] == [("altered_disclaimer", "brand_voice")]


def test_a_disclaimer_with_different_words_is_missing() -> None:
    other = "Illustrative disclaimer (synthetic rule): ask a professional first."
    assert kinds({"body": "Greens.", "footer": other}, t=truth(SUPPLEMENTS, RULES)) == [
        "missing_disclaimer"
    ]


def test_a_superlative_without_a_verified_source_blocks() -> None:
    assert kinds({"body": "The best trail shoe."}) == ["unsupported_superlative"]
    assert kinds({"body": "India's first tree sneaker."}) == ["unsupported_superlative"]


def test_a_superlative_the_cited_verified_claim_states_is_allowed() -> None:
    copy = {"body": "Our fastest drying sole yet."}
    assert kinds(copy, (Statement("fastest drying sole", "v2"),)) == []


def test_a_context_dependent_superlative_without_a_source_is_flagged_not_blocked() -> None:
    report = evaluate({"body": "The only shoe you need."}, (), truth(), LEXICON, TODAY)
    assert [(f.kind, f.outcome) for f in report.findings] == [("context_superlative", "flag")]
    assert report.outcome == "flag"


def test_a_flag_tier_word_inside_a_block_tier_phrase_is_reported_once_as_a_block() -> None:
    assert kinds({"body": "A market leading trail shoe."}) == ["unsupported_superlative"]
    assert kinds({"body": "Leading to drier feet."}) == ["context_superlative"]


def test_back_to_back_repeats_of_a_block_phrase_do_not_leak_to_the_flag_tier() -> None:
    assert kinds({"body": "Market leading, market leading grip."}) == ["unsupported_superlative"]


def test_a_context_superlative_the_cited_verified_claim_states_passes() -> None:
    cork = Claim("v3", "The only sole made of cork", "verified", "https://x.invalid", False)
    t = ProductTruth(FOOTWEAR, (cork,), ())
    copy = {"body": "The only sole made of cork."}
    assert kinds(copy, (Statement("The only sole made of cork", "v3"),), t) == []


def test_a_superlative_the_cited_claim_does_not_state_still_blocks() -> None:
    copy = {"body": "The fastest recycled mesh upper."}
    assert kinds(copy, (Statement("fastest recycled mesh upper", "v1"),)) == [
        "unsupported_superlative"
    ]


# ── Coverage, provenance, failure ────────────────────────────────────────────────────────────


def test_no_sector_or_a_regulated_sector_without_rules_blocks() -> None:
    assert kinds({"body": "Greens."}, t=truth(None)) == ["registry_coverage"]
    assert kinds({"body": "Greens."}, t=truth(SUPPLEMENTS)) == ["registry_coverage"]


def test_every_report_names_the_sector_with_its_provenance() -> None:
    report = evaluate({"body": "Greens."}, (), truth(SUPPLEMENTS, RULES), LEXICON, TODAY)
    assert "illustrative rules: synthetic, not derived from any regulation" in report.sector


def test_an_invalid_rule_blocks() -> None:
    broken = (_rule("bad", "prohibited_phrase", "(unclosed", "regex"),)
    assert kinds({"body": "Greens."}, t=truth(SUPPLEMENTS, broken)) == ["registry_rule_invalid"]


def test_an_error_inside_a_gate_is_a_block_never_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object) -> bool:
        raise RuntimeError("matcher exploded")

    monkeypatch.setattr(gates, "contains_phrase", boom)
    report = evaluate({"body": "Greens."}, (), truth(), LEXICON, TODAY)
    assert [f.kind for f in report.findings] == ["gate_error"]
    assert report.outcome == "block"


def test_voice_scoring_ignores_prescribed_disclaimer_text() -> None:
    copy = {"body": "Bright, plain-spoken greens.", "footer": ALWAYS}
    assert (
        voice_scoring_text(copy, truth(SUPPLEMENTS, RULES), TODAY) == "Bright, plain-spoken greens."
    )


# ── The superlative list is versioned and locked ─────────────────────────────────────────────


def test_the_released_list_loads_with_both_tiers_and_t3s_examples_block() -> None:
    lexicon = load_superlatives()
    assert lexicon.version == "v1"
    assert {"best", "number one", "fastest"} <= set(lexicon.block)  # Rules.md T3's examples
    assert {"leading", "the only", "ultimate"} <= set(lexicon.flag)


def test_a_phrase_on_both_tiers_is_refused(tmp_path: Path) -> None:
    text = (gates.SUPERLATIVES_DIR / "v1.yaml").read_text(encoding="utf-8") + "  - best\n"
    (tmp_path / "v1.yaml").write_text(text, encoding="utf-8")
    lock = f"v1 sha256:{gates.file_sha256(tmp_path / 'v1.yaml')}\n"
    (tmp_path / "versions.lock").write_text(lock, encoding="utf-8")
    with pytest.raises(ValueError, match="on both tiers"):
        load_superlatives("v1", tmp_path)


def test_an_edited_released_list_is_refused(tmp_path: Path) -> None:
    for name in ("v1.yaml", "versions.lock"):
        shutil.copy(gates.SUPERLATIVES_DIR / name, tmp_path / name)
    path = tmp_path / "v1.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "  - greenest\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match versions.lock"):
        load_superlatives("v1", tmp_path)
