"""Threat V12: under a revision loop an unsourced-claim finding is satisfiable by citing ANY active
claim, because the gate verifies that a citation resolves, not that it entails.

The instance, R10, 2026-10-30: "lightweight walking shoe" was reported with no claim on draft 1,
kept in draft 2 and cited to "Lace-up, low top, medium width, almond toe, low arch", and the gate
PASSED it. `citation_lacks_term` (app/core/citation_terms.py) is a WORD-OVERLAP test: the cited
claim's own text must contain what the statement asserts. These tests pin what it does on that exact
draft and, as plainly, what it does not catch: it is not entailment, it misses paraphrase in both
directions, and it cannot see what the extractor never extracted (V11).
"""

import pytest

from app.agents.copywriter import PROBLEM_WORDS, problems_text
from app.core.citation_terms import (
    content_terms,
    missing_terms,
    neutral_stems,
    stem,
)
from app.core.gates import GateReport, Statement, evaluate, load_superlatives
from tests.test_claim_classifier import TODAY, check, model_says
from tests.test_v11_audience_and_run_columns import R10_TRUTH

pytestmark = pytest.mark.anyio

LEXICON = load_superlatives()
NEUTRAL = neutral_stems(["walking shoes", "Pagdandi Stroll Lite"])

# Draft 2 of run X (2026-10-30), exactly as the reviser wrote it.
DRAFT_2 = {
    "headline": "Step into your commute with confidence",
    "body": (
        "If you walk to and from transit for your workday, discover a lightweight walking shoe "
        "designed with a knit upper, plus a rubber and EVA sole. This design features a lace-up, "
        "low top, medium width, almond toe, and low arch. Please note that this product is not "
        "water resistant."
    ),
    "call_to_action": "Explore the shoe",
}
# What the classifier said about it that time: the attribute CITED to the lace-up claim (c3).
SAID_ON_DRAFT_2 = (
    ("lightweight walking shoe", "c3"),
    ("knit upper", "c1"),
    ("rubber and EVA sole", "c2"),
    ("lace-up", "c3"),
    ("low top", "c3"),
    ("medium width", "c3"),
    ("almond toe", "c3"),
    ("low arch", "c3"),
    ("this product is not water resistant", "c4"),
)


def gate(said: tuple[tuple[str, str | None], ...]) -> GateReport:
    return evaluate(
        DRAFT_2,
        [Statement(text, claim) for text, claim in said],
        R10_TRUTH,
        LEXICON,
        TODAY,
        neutral_terms=NEUTRAL,
    )


# ── The exact draft ──────────────────────────────────────────────────────────────────────────


def test_the_draft_that_passed_on_2026_10_30_is_now_blocked_for_that_citation_alone() -> None:
    report = gate(SAID_ON_DRAFT_2)
    assert report.outcome == "block"
    (finding,) = report.findings  # the other eight statements still resolve
    assert finding.kind == "citation_lacks_term" and finding.refs == ("c3",)
    assert "'lightweight'" in finding.message and "lightweight walking shoe" in finding.message
    assert "lightweight walking shoe" not in [r.statement for r in report.resolutions]
    assert len(report.resolutions) == 8


@pytest.mark.parametrize(("quote", "claim"), SAID_ON_DRAFT_2[1:])
def test_each_legitimate_statement_of_that_draft_still_passes(quote: str, claim: str) -> None:
    report = gate(((quote, claim),))
    assert report.findings == (), report.findings


def test_citing_nothing_is_still_the_old_finding_and_not_this_one() -> None:
    report = gate((("lightweight walking shoe", None),))
    assert [f.kind for f in report.findings] == ["unsourced_claim"]


def test_the_finding_is_a_block_in_the_claim_gate_and_names_a_reviser_word() -> None:
    (finding,) = gate(SAID_ON_DRAFT_2).findings
    assert (finding.gate, finding.outcome) == ("claim", "block")
    assert PROBLEM_WORDS["citation_lacks_term"]
    # the reviser is shown the span (owner, 2026-10-31), the span only: the same conditions as
    # `unsourced_claim`, because a citation mismatch is not a restricted phrase
    text = problems_text(
        {
            "findings": [
                {"kind": "citation_lacks_term", "span": "lightweight walking shoe", "message": "M"}
            ]
        }
    )
    assert PROBLEM_WORDS["citation_lacks_term"] in text and '"lightweight walking shoe"' in text
    assert "M" not in text.replace(PROBLEM_WORDS["citation_lacks_term"], "").replace("remove", "")


def test_the_finding_records_the_statement_and_the_cited_claim_for_the_review_by_hand() -> None:
    """Pre-registered (docs/evaluation-plan.md): every citation_lacks_term finding keeps what is
    needed to judge it later, whether the claim in fact supports the statement."""
    (finding,) = gate(SAID_ON_DRAFT_2).findings
    assert finding.statement == "lightweight walking shoe"
    assert finding.claim_text == "Lace-up, low top, medium width, almond toe, low arch"
    assert finding.span == "lightweight walking shoe"
    from app.core.gates import finding_dict

    stored = finding_dict(finding)
    assert stored["statement"] and stored["claim_text"] and stored["kind"] == "citation_lacks_term"


def test_no_other_finding_kind_carries_a_statement_or_claim_text() -> None:
    report = gate((("lightweight walking shoe", None),))
    (f,) = report.findings
    assert (f.kind, f.statement, f.claim_text) == ("unsourced_claim", None, None)


# ── What it does, term by term ───────────────────────────────────────────────────────────────


def test_a_short_statement_needs_every_asserted_term_in_the_claim() -> None:
    assert missing_terms("lightweight walking shoe", "Knit upper", NEUTRAL) == ["lightweight"]
    assert missing_terms("knit upper", "Knit upper", NEUTRAL) == []
    assert missing_terms("rubber and EVA sole", "Rubber and EVA sole", NEUTRAL) == []
    assert missing_terms("knit and rubber", "Knit upper", NEUTRAL) == ["rubber"]


def test_the_words_that_name_the_product_are_not_something_it_asserts() -> None:
    assert content_terms("this walking shoe", NEUTRAL) == []
    assert missing_terms("this walking shoe", "Knit upper", NEUTRAL) == []
    assert stem("shoes") == stem("shoe") and stem("walking") == stem("walk")
    assert stem("boxes") == "box" and stem("batteries") == "battery" and stem("glass") == "glass"


def test_a_long_statement_needs_one_term_in_common() -> None:
    sentence = "You will find a sturdy collapsible hinge, a woven strap and a padded collar."
    assert missing_terms(sentence, "Padded collar", NEUTRAL) == []
    assert missing_terms(sentence, "Knit upper", NEUTRAL) != []


def test_negation_is_a_term_so_a_statement_cannot_borrow_the_opposite_claim() -> None:
    assert missing_terms("water resistant", "Not water resistant", NEUTRAL) == []  # a MISS, below
    assert missing_terms("not water resistant", "Water resistant", NEUTRAL) == ["not"]


# ── What it does NOT catch (stated, and pinned so nobody reads it as more) ───────────────────


def test_it_blocks_a_correct_citation_that_is_merely_worded_differently() -> None:
    """A false block: the claim says 'light', the copy says 'lightweight' (reword to fix)."""
    assert missing_terms("lightweight", "Light and breathable", NEUTRAL) == ["lightweight"]


def test_it_passes_a_property_word_the_claim_only_negates_or_mentions_in_passing() -> None:
    assert missing_terms("lightweight", "Not lightweight", NEUTRAL) == []
    claim = "Heavier than the previous model, but lightweight laces"
    assert missing_terms("lightweight", claim, NEUTRAL) == []  # same word, different thing


def test_it_passes_a_long_mis_cited_sentence_that_shares_one_incidental_word() -> None:
    sentence = "A featherlight, cloud-soft, all-day-cushioned knit upper for the whole commute."
    assert (
        missing_terms(sentence, "Knit upper", NEUTRAL) == []
    )  # 'knit upper' is real, the rest not


async def test_it_does_not_see_a_property_the_extractor_never_extracted() -> None:
    """V11 is upstream of this: no statement, no citation, nothing to compare."""
    report, _, _ = await check(DRAFT_2, R10_TRUTH, model_says(("knit upper", "c1")), strong=False)
    assert report.outcome == "pass"  # 'lightweight' is in the copy and judged by nobody


# ── The loop can no longer pass by re-citing ─────────────────────────────────────────────────


async def test_a_revision_that_keeps_the_attribute_and_cites_another_claim_is_blocked_again() -> (
    None
):
    from app.agents.copywriter import CopyWriter
    from app.agents.mock import mock_agents
    from app.orchestrator.adapters import classifier_extractor
    from tests.llm_fakes import FakeCompletion, prompt_library
    from tests.test_copywriter import draft, gateway
    from tests.test_orchestrator_graph import Harness

    completion = FakeCompletion(
        # draft 1 (the mock A6's copy is replaced below): the attribute with no claim
        model_says(("lightweight walking shoe", None)),
        # the reviser keeps it; the classifier now cites c3 for it (what happened live)
        draft(headline="Step in", body="Discover a lightweight walking shoe with a knit upper."),
        model_says(("lightweight walking shoe", "c3"), ("knit upper", "c1")),
        # a third draft without it
        draft(headline="Step in", body="A knit upper and a rubber and EVA sole."),
        model_says(("knit upper", "c1"), ("rubber and EVA sole", "c2")),
    )
    gw, _ = gateway(completion)
    prompts = prompt_library()
    from app.agents.outputs import CreativeOutput
    from tests.test_v11_audience_and_run_columns import R10_COPY

    class FirstThenReal:
        id = "A6"

        def __init__(self) -> None:
            self.real = CopyWriter(gw, prompts)

        async def run(self, state, ctx):  # type: ignore[no-untyped-def]
            if state.get("creative") is None:
                return CreativeOutput(copy=R10_COPY)
            return await self.real.run(state, ctx)

    agents = {**mock_agents(), "A6": FirstThenReal()}
    out = await Harness(
        truth=R10_TRUTH, agents=agents, extractor=classifier_extractor(gw, prompts)
    ).start()
    # Before V12 the second draft PASSED (round 1, outcome pass) with the attribute in it.
    assert out.state["revision_round"] == 2
    assert out.state["validation"]["outcome"] == "pass"
    assert out.state["validation"]["findings"] == []


async def test_every_blocked_draft_is_handed_to_the_recorder_with_its_round() -> None:
    """Blocked and logged (T2), in the graph: the review needs the findings of every round."""
    from app.agents.copywriter import CopyWriter
    from app.agents.mock import mock_agents
    from app.agents.outputs import CreativeOutput
    from app.orchestrator.adapters import classifier_extractor
    from tests.llm_fakes import FakeCompletion, prompt_library
    from tests.test_copywriter import draft, gateway
    from tests.test_orchestrator_graph import Harness
    from tests.test_v11_audience_and_run_columns import R10_COPY

    completion = FakeCompletion(
        model_says(("lightweight walking shoe", "c3")),  # draft 1: mis-cited, blocked
        draft(headline="Step in", body="Discover a lightweight walking shoe."),
        model_says(("lightweight walking shoe", "c3")),  # draft 2: the same escape, blocked again
        draft(headline="Step in", body="A knit upper and a rubber and EVA sole."),
        model_says(("knit upper", "c1"), ("rubber and EVA sole", "c2")),
    )
    gw, _ = gateway(completion)
    prompts = prompt_library()

    class FirstThenReal:
        id = "A6"

        def __init__(self) -> None:
            self.real = CopyWriter(gw, prompts)

        async def run(self, state, ctx):  # type: ignore[no-untyped-def]
            if state.get("creative") is None:
                return CreativeOutput(copy=R10_COPY)
            return await self.real.run(state, ctx)

    from tests.test_orchestrator_graph import Harness as H

    h = H(
        truth=R10_TRUTH,
        agents={**mock_agents(), "A6": FirstThenReal()},
        extractor=classifier_extractor(gw, prompts),
    )
    seen: list[tuple[int, list[dict[str, object]]]] = []

    async def record_block(state, report):  # type: ignore[no-untyped-def]
        from app.core.gates import finding_dict

        seen.append((state.get("revision_round", 0), [finding_dict(f) for f in report.findings]))

    h.deps.record_block = record_block
    out = await h.start()
    assert out.state["validation"]["outcome"] == "pass" and out.state["revision_round"] == 2
    assert [r for r, _ in seen] == [0, 1]  # the two blocked drafts, not the passing third
    for _, findings in seen:
        (f,) = findings
        assert f["kind"] == "citation_lacks_term"
        assert f["statement"] == "lightweight walking shoe" and f["claim_text"]
    assert Harness is H


# ── A labelled deduction is exempt, and the flag says so (owner, 2026-10-31) ─────────────────


def _deduction_truth():  # type: ignore[no-untyped-def]
    from app.core.sector_registry import Sector
    from app.core.truth import Claim, ProductTruth

    return ProductTruth(
        Sector("footwear", regulated=False),
        (
            Claim("d1", "About 300 charge cycles before fading", "deduced", None, False),
            Claim("v1", "Knit upper", "verified", "https://example.com/spec", False),
        ),
        (),
    )


def _eval(copy: str, statement: Statement):  # type: ignore[no-untyped-def]
    return evaluate({"body": copy}, [statement], _deduction_truth(), LEXICON, TODAY)


def test_a_labelled_deduction_is_flagged_not_blocked_and_the_flag_says_it_was_not_checked() -> None:
    report = _eval(
        "We think it lasts for years.",
        Statement("it lasts for years", "d1", labelled_deduction=True),
    )
    kinds = [f.kind for f in report.findings]
    assert "citation_lacks_term" not in kinds and report.outcome == "flag"
    (flag, *_) = [f for f in report.findings if f.kind == "deduced_labelled"]
    assert flag.outcome == "flag"
    assert "NOT lexically checked" in flag.message
    assert "reviewer judges whether the cited claim supports the inference" in flag.message


def test_the_label_is_no_escape_for_a_verified_citation() -> None:
    """Labelling a statement an inference does not exempt it when it cites a VERIFIED claim."""
    report = _eval(
        "We think it is lightweight.",
        Statement("it is lightweight", "v1", labelled_deduction=True),
    )
    assert [f.kind for f in report.findings] == ["citation_lacks_term"]


def test_an_unlabelled_deduction_is_still_the_old_block_and_not_this_one() -> None:
    report = _eval("It lasts for years.", Statement("It lasts for years.", "d1"))
    assert [f.kind for f in report.findings] == ["deduced_unlabelled"]


def test_known_cost_a_property_labelled_as_an_inference_from_a_deduction_is_only_flagged() -> None:
    """The exemption's cost, stated: 'we think it is lightweight' cited to a DEDUCED claim passes
    as a flag (as a labelled deduction always did), with the not-checked notice for the approver."""
    report = _eval(
        "We think it is lightweight.",
        Statement("it is lightweight", "d1", labelled_deduction=True),
    )
    assert report.outcome == "flag"
    assert [f.kind for f in report.findings] == ["deduced_labelled", "unrepeated_quantity"]
