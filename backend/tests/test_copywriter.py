"""A6, the copy writer, and step 11 on the real classifier path (Phases.md Phase 6 chunk B).

Mocked model, no network. What must hold: the model is shown only claims it may use and nothing it
may not (Rules.md T2); it never supplies the statements or the disclaimers; a rejected draft is
described to it in fixed words that cannot carry the draft's own text; a budget refusal ends the run
partial instead of becoming a block; and step 15 re-judges the extracted statements without another
model call.
"""

import json
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agents.base import AgentContext
from app.agents.copywriter import PROBLEM_WORDS, CopyWriter, problems_text
from app.agents.mock import mock_agents
from app.core.gates import FindingKind, load_superlatives, required_disclaimers
from app.core.sector_registry import Sector, SectorConstraint
from app.core.truth import Claim, ProductTruth
from app.errors import BudgetExceeded
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext
from app.llm.registry import Tier
from app.orchestrator.adapters import classifier_extractor
from tests.llm_fakes import FakeCompletion, FakeLedger, make_registry, prompt_library, reply
from tests.test_orchestrator_graph import Harness, truth_with

pytestmark = pytest.mark.anyio

ON = date(2026, 10, 29)
SINCE = date(2026, 1, 1)
LEXICON = load_superlatives("v1")
V1 = Claim("c1", "Battery lasts 8 hours", "verified", "https://example.com/spec", False)
D1 = Claim("d1", "Likely to suit daily commutes", "deduced", None, False)
R1 = Claim("r1", "cures anxiety", "restricted", None, False)
OLD = Claim("o1", "Waterproof to 50 metres", "verified", "https://example.com/old", True)

ALWAYS = "Consult a professional before use."
PROTEIN = "Contains 25 g of protein per serving."
REGULATED = Sector("health-supplements", regulated=True)
RULES = (
    SectorConstraint(
        "p1", REGULATED.id, "prohibited_phrase", SINCE, None, "phrase", "miracle cure"
    ),
    SectorConstraint(
        "d1", REGULATED.id, "required_disclaimer", SINCE, None, "phrase", None, ALWAYS
    ),
    SectorConstraint(
        "d2", REGULATED.id, "required_disclaimer", SINCE, None, "phrase", "protein", PROTEIN
    ),
)


class CapturingLedger(FakeLedger):
    """The fake ledger, remembering the context of each call it admitted."""

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[CallContext] = []

    async def admit(self, ctx: CallContext, *args: Any, **kwargs: Any) -> Any:
        self.contexts.append(ctx)
        return await super().admit(ctx, *args, **kwargs)


def gateway(completion: FakeCompletion) -> tuple[LLMGateway, CapturingLedger]:
    ledger = CapturingLedger()
    gw = LLMGateway(
        make_registry(unique=False),
        ledger,  # type: ignore[arg-type]
        prompt_library(),
        api_key="test-key",
        timeout_s=30,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        allow_strong_tier=False,
        completion=completion,
    )
    return gw, ledger


def draft(
    headline: str = "Go further.", body: str = "Battery lasts 8 hours.", cta: str = "Shop now"
):
    return reply(json.dumps({"headline": headline, "body": body, "call_to_action": cta}))


def statements(*pairs: tuple[str, str | None]) -> object:
    return reply(json.dumps({"statements": [{"quote": q, "claim_id": c} for q, c in pairs]}))


def agent_ctx(truth: ProductTruth, *, step: int = 10, round_: int = 0) -> AgentContext:
    return AgentContext(
        uuid4(), uuid4(), truth, ON, step=step, revision_round=round_, execution_id=uuid4()
    )


def sent(completion: FakeCompletion, call: int = 0) -> str:
    return "\n".join(m["content"] for m in completion.calls[call]["messages"])


# ── What the model is shown ──────────────────────────────────────────────────────────────────


async def test_the_model_sees_verified_and_deduced_claims_only() -> None:
    completion = FakeCompletion(draft())
    gw, ledger = gateway(completion)
    truth = truth_with(V1, D1, R1, OLD)
    out = await CopyWriter(gw, prompt_library()).run({"brief": "Launch push"}, agent_ctx(truth))
    prompt = sent(completion)
    assert "c1 | verified | Battery lasts 8 hours" in prompt and "d1 | deduced" in prompt
    assert "cures anxiety" not in prompt and "Waterproof to 50 metres" not in prompt
    assert "Launch push" in prompt
    assert out.copy_ == {
        "headline": "Go further.",
        "body": "Battery lasts 8 hours.",
        "call_to_action": "Shop now",
    }


async def test_the_call_is_standard_tier_and_carries_step_round_and_execution() -> None:
    completion = FakeCompletion(draft())
    gw, ledger = gateway(completion)
    ctx = agent_ctx(truth_with(V1), step=10, round_=2)
    await CopyWriter(gw, prompt_library()).run({}, ctx)
    (row,) = ledger.rows.values()
    assert row["tier"] == Tier.STANDARD.value and row["route_reason"] == "A6 creative: default tier"
    (seen,) = ledger.contexts
    assert (seen.agent_id, seen.step, seen.revision_round) == ("A6", 10, 2)
    assert seen.execution_id == ctx.execution_id and seen.run_id == ctx.run_id
    assert row["prompt"].prompt_id == "a6_copy"


async def test_an_agent_context_with_no_execution_cannot_make_a_call() -> None:
    gw, _ = gateway(FakeCompletion(draft()))
    bare = AgentContext(uuid4(), uuid4(), truth_with(V1), ON)
    with pytest.raises(ValueError, match="need the graph step"):
        await CopyWriter(gw, prompt_library()).run({}, bare)


async def test_an_empty_call_to_action_is_left_out_of_the_copy() -> None:
    gw, _ = gateway(FakeCompletion(draft(cta="")))
    out = await CopyWriter(gw, prompt_library()).run({}, agent_ctx(truth_with(V1)))
    assert sorted(out.copy_) == ["body", "headline"]


# ── Disclaimers come from the registry, by code ──────────────────────────────────────────────


def regulated(*claims: Claim) -> ProductTruth:
    return ProductTruth(REGULATED, claims, RULES)


async def test_required_disclaimers_are_appended_verbatim_and_the_gate_finds_them() -> None:
    gw, _ = gateway(FakeCompletion(draft(body="Battery lasts 8 hours. Packed with protein.")))
    truth = regulated(V1)
    out = await CopyWriter(gw, prompt_library()).run({}, agent_ctx(truth))
    assert (
        out.copy_["footer"] == f"{ALWAYS}\n{PROTEIN}"
    )  # the protein rule is triggered by the copy
    from app.core.gates import Statement, evaluate

    report = evaluate(out.copy_, [Statement("Battery lasts 8 hours", "c1")], truth, LEXICON, ON)
    assert not [f for f in report.findings if "disclaimer" in f.kind]


async def test_only_the_disclaimers_the_copy_triggers_are_added() -> None:
    gw, _ = gateway(FakeCompletion(draft(body="Battery lasts 8 hours.")))
    out = await CopyWriter(gw, prompt_library()).run({}, agent_ctx(regulated(V1)))
    assert out.copy_["footer"] == ALWAYS


async def test_a_non_regulated_product_gets_no_footer() -> None:
    gw, _ = gateway(FakeCompletion(draft()))
    out = await CopyWriter(gw, prompt_library()).run({}, agent_ctx(truth_with(V1)))
    assert "footer" not in out.copy_


def test_a_disclaimers_own_wording_can_trigger_another() -> None:
    chain = ProductTruth(
        REGULATED,
        (),
        (
            SectorConstraint(
                "a",
                REGULATED.id,
                "required_disclaimer",
                SINCE,
                None,
                "phrase",
                "dose",
                "Take as directed, see the warning.",
            ),
            SectorConstraint(
                "b",
                REGULATED.id,
                "required_disclaimer",
                SINCE,
                None,
                "phrase",
                "warning",
                "Warning: keep out of reach.",
            ),
        ),
    )
    assert required_disclaimers({"body": "Check the dose."}, chain, ON) == [
        "Take as directed, see the warning.",
        "Warning: keep out of reach.",
    ]
    assert required_disclaimers({"body": "Hello."}, chain, ON) == []


# ── What a rejected draft is told ────────────────────────────────────────────────────────────


def test_every_finding_kind_has_words_and_none_is_empty() -> None:
    from typing import get_args

    assert set(PROBLEM_WORDS) == set(get_args(FindingKind))
    assert all(w.strip() for w in PROBLEM_WORDS.values())


async def test_a_findings_message_and_the_drafts_text_never_reach_the_next_prompt() -> None:
    leaky = {
        "findings": [
            {
                "kind": "unsourced_claim",
                "message": "the statement 'cures anxiety in a week' is not backed",
                "refs": ["r1"],
            },
            {"kind": "restricted_claim_in_copy", "message": "cures anxiety", "refs": ["r1"]},
        ]
    }
    completion = FakeCompletion(draft())
    gw, _ = gateway(completion)
    await CopyWriter(gw, prompt_library()).run({"validation": leaky}, agent_ctx(truth_with(V1, R1)))
    prompt = sent(completion)
    assert "cures anxiety" not in prompt
    assert PROBLEM_WORDS["unsourced_claim"] in prompt
    assert PROBLEM_WORDS["restricted_claim_in_copy"] in prompt


def test_a_first_draft_has_no_problems_and_an_unknown_kind_gets_a_generic_line() -> None:
    assert problems_text(None) == "(none)" and problems_text({"findings": []}) == "(none)"
    assert "a check flagged the draft" in problems_text({"findings": [{"kind": "no_creative"}]})


async def test_a_budget_refusal_is_not_swallowed_by_the_writer() -> None:
    completion = FakeCompletion(draft())
    gw, ledger = gateway(completion)
    ledger.refusal = BudgetExceeded("run call cap")
    with pytest.raises(BudgetExceeded):
        await CopyWriter(gw, prompt_library()).run({}, agent_ctx(truth_with(V1)))
    assert completion.calls == []  # refused before any provider call


# ── Step 11 on the real classifier path, inside the graph ────────────────────────────────────


def real_harness(*replies: object, truth: ProductTruth | None = None, strong: bool = False):
    completion = FakeCompletion(*replies)
    gw, ledger = gateway(completion)
    prompts = prompt_library()
    agents = {**mock_agents(), "A6": CopyWriter(gw, prompts)}
    h = Harness(
        truth=truth or truth_with(V1),
        agents=agents,
        extractor=classifier_extractor(gw, prompts),
    )
    return h, completion, ledger


async def test_the_run_extracts_statements_with_the_classifier_and_never_asks_the_writer() -> None:
    h, completion, ledger = real_harness(
        draft(body="Battery lasts 8 hours."), statements(("Battery lasts 8 hours", "c1"))
    )
    out = await h.start()
    assert out.status == "suspended" and out.state["validation"]["outcome"] == "pass"
    assert out.state["validation"]["statements"] == [
        {"text": "Battery lasts 8 hours", "claim_id": "c1", "labelled_deduction": False}
    ]
    steps = [(c.agent_id, c.step, c.revision_round) for c in ledger.contexts]
    assert steps == [("A6", 10, 0), ("A7", 11, 0)]
    assert ledger.contexts[0].execution_id != ledger.contexts[1].execution_id


async def test_step_15_rejudges_the_stored_statements_without_another_model_call() -> None:
    h, completion, _ = real_harness(
        draft(body="Battery lasts 8 hours."), statements(("Battery lasts 8 hours", "c1"))
    )
    await h.start()
    assert len(completion.calls) == 2
    await h.decide("approve")
    assert len(completion.calls) == 2, "step 15 used the stored statements"
    assert h.memory_calls == [{"decision": "approve", "report": "pass"}]


async def test_an_unsourced_statement_blocks_and_the_next_round_is_told_where() -> None:
    h, completion, _ = real_harness(
        draft(body="Battery lasts 8 hours and doubles as a torch."),
        statements(("doubles as a torch", None)),
        draft(body="Battery lasts 8 hours."),
        statements(("Battery lasts 8 hours", "c1")),
    )
    out = await h.start()
    assert out.state["revision_round"] == 1 and out.state["validation"]["outcome"] == "pass"
    second_prompt = sent(completion, 2)
    assert PROBLEM_WORDS["unsourced_claim"] in second_prompt
    # Changed 2026-10-30 (V11): an unsourced statement is named, by its span alone, so the reviser
    # can remove it. The message and the rest of the draft are still not handed back, and every
    # other kind keeps the fixed words (tests/test_brief_span_and_a1_v2.py).
    assert '"doubles as a torch"' in second_prompt
    assert "cites no claim" not in second_prompt and "and doubles" not in second_prompt


async def test_a_failed_extraction_is_a_gate_error_block_and_stays_one_at_step_15() -> None:
    h, completion, _ = real_harness(draft(), RuntimeError("provider down"))
    h.max_revisions = 0
    h.deps.max_revisions = 0
    out = await h.start()
    kinds = [f["kind"] for f in out.state["validation"]["findings"]]
    assert kinds == ["gate_error"] and out.state["validation"]["statements"] is None
    await h.decide("approve")
    assert h.memory_calls == [{"decision": "approve", "report": "block"}]


async def test_a_budget_refusal_at_step_11_ends_the_run_partial_not_blocked() -> None:
    h, _, ledger = real_harness(draft())

    async def refused(ctx: AgentContext, copy: Any) -> Any:
        raise BudgetExceeded("run call cap")

    h.deps.extract_statements = refused
    out = await h.start()
    assert out.status == "partial" and h.ledger.calls[-1][:3] == ("end", "partial", True)


async def test_the_classifier_itself_lets_a_budget_refusal_through() -> None:
    from app.core.claim_classifier import check_copy

    completion = FakeCompletion()
    gw, ledger = gateway(completion)
    ledger.refusal = BudgetExceeded("run call cap")
    with pytest.raises(BudgetExceeded):
        await check_copy(
            gw,
            prompt_library(),
            CallContext(uuid4(), uuid4(), "A7"),
            {"body": "x"},
            truth_with(V1),
            LEXICON,
            ON,
        )


async def test_a_regulated_sector_extraction_needs_the_strong_tier_and_fails_closed() -> None:
    truth = regulated(V1)
    h, completion, ledger = real_harness(draft(), truth=truth)
    h.deps.max_revisions = 0
    out = await h.start()
    kinds = [f["kind"] for f in out.state["validation"]["findings"]]
    assert kinds == ["gate_error"], "the strong tier is off outside the tuning organisation (T4)"
    assert len(completion.calls) == 1  # the writer ran; the classifier was refused before a call
    assert UUID(str(ledger.contexts[0].run_id))
