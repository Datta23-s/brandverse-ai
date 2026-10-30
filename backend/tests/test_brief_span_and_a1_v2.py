"""Three consequences of V11 (docs/decision-log.md), all offline.

1. A1's prompt v2: the brief is for intent, never for product properties. The exact case: the brief
   says "lightweight", the claims do not. These tests show what the model is TOLD; whether
   it obeys is a live observation.
2. The brief is checked at admission, FLAG AND RECORD: the property assertions no claim backs are
   listed in the run's state; nothing is blocked, a failed check does not stop the run.
3. The reviser is told the offending SPAN for `unsourced_claim` findings only. Every other kind, and
   any kind added later, reveals nothing: a restricted phrase the model wrote is not handed back.
"""

from collections.abc import Mapping
from typing import Any, get_args

import pytest

from app.agents.base import AgentContext
from app.agents.copywriter import (
    MAX_SPAN_CHARS,
    MAX_SPANS,
    PROBLEM_WORDS,
    REVEALS_SPAN,
    CopyWriter,
    problems_text,
)
from app.agents.outputs import CreativeOutput
from app.agents.product_brand import A1_POSITIONING, ProductBrandAnalyst
from app.core.gates import FindingKind, Statement
from app.core.sector_registry import Sector
from app.errors import BudgetExceeded
from app.orchestrator.adapters import brief_checker
from tests.llm_fakes import FakeCompletion, prompt_library
from tests.test_claim_classifier import check, model_says
from tests.test_copywriter import V1, agent_ctx, draft, gateway, sent
from tests.test_orchestrator_graph import Harness, truth_with
from tests.test_product_and_market_agents import positioning
from tests.test_v11_audience_and_run_columns import R10_COPY, R10_TRUTH, WHAT_V1_EXTRACTED

pytestmark = pytest.mark.anyio

R10_BRIEF = (
    "Launch awareness for the lightweight walking shoe. Audience: Office workers who walk to and "
    "from transit."
)


# ── 1. A1 v2 ─────────────────────────────────────────────────────────────────────────────────


def test_a1_uses_v2_and_v1_is_unchanged() -> None:
    assert A1_POSITIONING == ("a1_positioning", "v2")
    library = prompt_library()
    v1, v2 = library.get("a1_positioning", "v1"), library.get(*A1_POSITIONING)
    assert "built only from the listed claims and the brief" in (v1.system or "")  # the leak, kept
    assert "built only from the listed claims and the brief" not in (v2.system or "")


async def test_a1_is_told_the_brief_is_not_a_source_of_product_properties() -> None:
    """The exact case: the brief says lightweight, the claims do not."""
    completion = FakeCompletion(positioning("For office workers who walk to transit"))
    gw, _ = gateway(completion)
    await ProductBrandAnalyst(gw, prompt_library()).run(
        {"brief": R10_BRIEF, "product": {"category": "walking shoes"}},
        agent_ctx(R10_TRUTH, step=7),
    )
    prompt = sent(completion)
    claims_block = prompt[prompt.index("Listed claims") : prompt.index("Category:")]
    assert "lightweight" not in claims_block  # no listed claim says it
    assert "lightweight walking shoe" in prompt  # the brief is still shown, for intent
    assert "Do NOT take any property of the product from it" in prompt
    assert "not a source of product facts" in prompt
    assert "WHO it is for" in prompt  # what the brief IS for


# ── 2. The brief check at admission ──────────────────────────────────────────────────────────


def with_check(h: Harness, said: list[Statement] | Exception) -> list[AgentContext]:
    seen: list[AgentContext] = []

    async def check_brief(ctx: AgentContext, texts: Mapping[str, str]) -> list[Statement]:
        seen.append(ctx)
        assert set(texts) == {"brief"}
        if isinstance(said, Exception):
            raise said
        return said

    h.deps.check_brief = check_brief
    return seen


BRIEF = "Push the earphones. Battery lasts 8 hours and they are lightweight earphones."


async def test_the_unbacked_property_is_listed_and_the_run_is_not_blocked() -> None:
    h = Harness(truth=truth_with(V1))
    seen = with_check(
        h,
        [
            Statement("Battery lasts 8 hours", "c1"),
            Statement("lightweight earphones", None),
            Statement("a sentence the brief never contained", None),  # not a quote: ignored
        ],
    )
    out = await h.start(brief=BRIEF)
    assert out.status == "suspended"  # parked at approval: nothing blocked
    assert out.state["brief_check"] == {
        "status": "checked",
        "unbacked": ["lightweight earphones"],
        "backed": 1,
        "prompt": "claim_extract@v2",
    }
    assert [(c.step, c.run_id) for c in seen] == [(3, h.run_id)]


async def test_a_citation_of_a_claim_the_model_was_not_shown_counts_as_unbacked() -> None:
    h = Harness(truth=truth_with(V1))
    with_check(h, [Statement("lightweight earphones", "not-a-claim")])
    out = await h.start(brief=BRIEF)
    assert out.state["brief_check"]["unbacked"] == ["lightweight earphones"]


async def test_a_failed_check_is_recorded_unavailable_and_the_run_goes_on() -> None:
    h = Harness(truth=truth_with(V1))
    with_check(h, RuntimeError("model down"))
    out = await h.start(brief=BRIEF)
    assert out.status == "suspended"
    assert out.state["brief_check"]["status"] == "unavailable"
    assert out.state["brief_check"]["unbacked"] == []


async def test_a_budget_refusal_at_admission_still_ends_the_run_partial() -> None:
    h = Harness(truth=truth_with(V1))
    with_check(h, BudgetExceeded("run call cap"))
    assert (await h.start(brief=BRIEF)).status == "partial"


async def test_no_brief_or_no_checker_means_no_call() -> None:
    h = Harness(truth=truth_with(V1))
    seen = with_check(h, [])
    assert (await h.start(brief=None)).state["brief_check"]["status"] == "skipped"
    assert seen == []
    bare = Harness(truth=truth_with(V1))  # no check_brief wired
    assert (await bare.start(brief=BRIEF)).state["brief_check"]["status"] == "skipped"


async def test_a_run_admission_blocks_does_not_spend_a_call_on_the_check() -> None:
    from app.core.truth import Claim

    restricted = Claim("r1", "cures anxiety", "restricted", None, False)
    h = Harness(truth=truth_with(V1, restricted))
    seen = with_check(h, [])
    out = await h.start(brief="Say the product cures anxiety.")
    assert out.status == "blocked" and seen == []


async def test_the_real_checker_logs_as_admission_at_step_3_on_the_standard_tier() -> None:
    """Even for a regulated sector: the brief is not output copy (T4's strong tier is for that)."""
    completion = FakeCompletion(model_says(("lightweight earphones", None)))
    gw, ledger = gateway(completion)
    truth = truth_with(V1, sector=Sector("health-supplements", regulated=True))
    ctx = agent_ctx(truth, step=3)
    out = await brief_checker(gw, prompt_library())(ctx, {"brief": BRIEF})
    assert [(s.text, s.claim_id) for s in out] == [("lightweight earphones", None)]
    (row,) = ledger.rows.values()
    assert row["tier"] == "standard" and "brief check at admission" in row["route_reason"]
    (seen,) = ledger.contexts
    assert (seen.agent_id, seen.step) == ("ADMISSION", 3)
    assert row["prompt"].prompt_id == "claim_extract" and row["prompt"].prompt_version == "v2"


# ── 3. The span, for unsourced_claim only ────────────────────────────────────────────────────


def finding(kind: str, span: str | None = None, message: str = "m") -> dict[str, Any]:
    return {"kind": kind, "message": message, "span": span}


def test_only_unsourced_claim_reveals_a_span() -> None:
    assert REVEALS_SPAN == {"unsourced_claim"}
    assert REVEALS_SPAN <= set(get_args(FindingKind))


def test_an_unsourced_claim_names_its_span_and_nothing_else() -> None:
    text = problems_text(
        {
            "findings": [
                finding(
                    "unsourced_claim",
                    "lightweight walking shoe",
                    "factual statement cites no claim: 'a message that quotes the copy'",
                )
            ]
        }
    )
    assert PROBLEM_WORDS["unsourced_claim"] in text
    assert '"lightweight walking shoe"' in text
    assert "message" not in text and "quotes the copy" not in text


def test_a_restricted_claim_finding_still_reveals_nothing() -> None:
    phrase = "cures anxiety in a week"
    text = problems_text(
        {"findings": [finding("restricted_claim_in_copy", phrase, f"copy says {phrase!r}")]}
    )
    assert phrase not in text and "cures" not in text
    assert text == f"- {PROBLEM_WORDS['restricted_claim_in_copy']}"


@pytest.mark.parametrize("kind", [k for k in get_args(FindingKind) if k != "unsourced_claim"])
def test_every_other_kind_reveals_nothing_even_with_a_span_set(kind: str) -> None:
    text = problems_text({"findings": [finding(kind, "SOMETHING THE MODEL WROTE")]})
    assert "SOMETHING THE MODEL WROTE" not in text


def test_a_kind_added_later_reveals_nothing_by_default() -> None:
    text = problems_text({"findings": [finding("a_future_kind", "SOMETHING THE MODEL WROTE")]})
    assert "SOMETHING THE MODEL WROTE" not in text
    assert "a check flagged the draft" in text


def test_spans_are_deduplicated_capped_in_number_and_length_and_cannot_close_their_quotes() -> None:
    spans = ["a", "a", "b", "c", "d", 'say "hi"', "x" * 500]
    text = problems_text({"findings": [finding("unsourced_claim", s) for s in spans]})
    assert text.count('"') == 2 * MAX_SPANS  # three quoted spans, no stray quote
    assert '"d"' not in text and "x" * (MAX_SPAN_CHARS + 1) not in text
    long_only = problems_text({"findings": [finding("unsourced_claim", "x" * 500)]})
    assert "x" * MAX_SPAN_CHARS in long_only and "x" * (MAX_SPAN_CHARS + 1) not in long_only
    quoted = problems_text({"findings": [finding("unsourced_claim", 'say "hi"')]})
    assert "say 'hi'" in quoted


async def test_the_gate_sets_a_span_on_unsourced_claim_findings_alone() -> None:
    from app.orchestrator.nodes import report_dict

    said = (
        *WHAT_V1_EXTRACTED,
        ("lightweight walking shoe", None),
        ("a knit upper", "no-such-claim"),
    )
    report, _, _ = await check(R10_COPY, R10_TRUTH, model_says(*said), strong=False)
    by_kind = {f["kind"]: f for f in report_dict(report)["findings"]}
    assert by_kind["unsourced_claim"]["span"] == "lightweight walking shoe"
    assert all(f["span"] is None for k, f in by_kind.items() if k != "unsourced_claim")


async def test_a6_is_shown_the_span_on_a_revision_and_not_the_rest_of_the_draft() -> None:
    completion = FakeCompletion(draft())
    gw, _ = gateway(completion)
    state: dict[str, Any] = {
        "brief": "Launch",
        "creative": {"copy": dict(R10_COPY)},
        "validation": {
            "findings": [
                finding("unsourced_claim", "lightweight walking shoe", "cites no claim: 'x'"),
                finding("prohibited_phrase", "a prohibited phrase", "uses a prohibited phrase"),
            ]
        },
    }
    out = await CopyWriter(gw, prompt_library()).run(state, agent_ctx(R10_TRUTH, step=10, round_=1))
    assert isinstance(out, CreativeOutput)
    prompt = sent(completion)
    assert '"lightweight walking shoe"' in prompt
    assert "a prohibited phrase" not in prompt
    assert "Transition smoothly" not in prompt and "Discover more today" not in prompt
