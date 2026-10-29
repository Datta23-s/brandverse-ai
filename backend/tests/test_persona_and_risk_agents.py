"""A3 (personas) and A7's risk read, mocked model, no network (Phases.md Phase 6 chunk B).

A3: personas are hypotheses; the model is shown only claims it may use; ids are ours; the summary is
the contract chunk C encodes. A7: a MODEL OPINION, NOT A DETECTOR. It never blocks and never changes
a gate result; it is reported in its own category apart from the deterministic findings; an invented
quote is dropped; and a failure is a declared gap, never a block (owner, 2026-10-29;
Rules.md T5 stays unmet, threat V3 stands).
"""

import json
from typing import Any

import pytest

from app.agents.copywriter import CopyWriter
from app.agents.mock import mock_agents
from app.agents.personas import MAX_TITLES, PersonaAnalyst, evidence_titles
from app.agents.risk import RiskReader
from app.errors import BudgetExceeded
from app.orchestrator.adapters import classifier_extractor
from tests.llm_fakes import FakeCompletion, prompt_library, reply
from tests.test_copywriter import (
    D1,
    R1,
    V1,
    agent_ctx,
    draft,
    gateway,
    sent,
    statements,
)
from tests.test_orchestrator_graph import Harness, truth_with

pytestmark = pytest.mark.anyio


def personas(*summaries: str) -> object:
    return reply(
        json.dumps(
            {
                "personas": [
                    {
                        "summary": s,
                        "jobs_to_be_done": ["get to work dry"],
                        "objections": ["will it last"],
                        "channels": ["short video"],
                    }
                    for s in summaries
                ]
            }
        )
    )


def flags(*items: tuple[str, str, str]) -> object:
    return reply(
        json.dumps({"flags": [{"category": c, "quote": q, "note": n} for c, q, n in items]})
    )


# ── A3 ───────────────────────────────────────────────────────────────────────────────────────


async def test_a3_is_shown_claims_it_may_use_the_category_and_titles_and_nothing_restricted() -> (
    None
):
    completion = FakeCompletion(
        personas("Commuters, 22-30, find gear on video, objection: durability")
    )
    gw, ledger = gateway(completion)
    state = {
        "brief": "Monsoon launch",
        "product": {"category": "neckband earphones"},
        "truth": {"sector": "consumer_electronics"},
        "findings": [{"title": "Best earphones for rain"}, {"title": None}],
    }
    out = await PersonaAnalyst(gw, prompt_library()).run(
        state, agent_ctx(truth_with(V1, D1, R1), step=7)
    )
    prompt = sent(completion)
    assert "c1 | verified | Battery lasts 8 hours" in prompt and "d1 | deduced" in prompt
    assert "cures anxiety" not in prompt
    assert "neckband earphones" in prompt and "consumer electronics" in prompt
    assert "- Best earphones for rain" in prompt and "Monsoon launch" in prompt
    (row,) = ledger.rows.values()
    assert row["tier"] == "standard" and row["route_reason"] == "A3 personas: default tier"
    (seen,) = ledger.contexts
    assert (seen.agent_id, seen.step) == ("A3", 7)
    assert [p.id for p in out.personas] == ["p1"]  # ids are assigned here, not by the model


async def test_a3_returns_up_to_three_personas_with_their_summaries_normalised() -> None:
    gw, _ = gateway(
        FakeCompletion(personas("  First   buyers \n 22-30 ", "Gym regulars", "Parents"))
    )
    out = await PersonaAnalyst(gw, prompt_library()).run({}, agent_ctx(truth_with(V1), step=7))
    assert [p.id for p in out.personas] == ["p1", "p2", "p3"]
    assert out.personas[0].summary == "First buyers 22-30"


def test_evidence_titles_are_capped_and_empty_reads_none() -> None:
    assert evidence_titles([]) == "(none)" and evidence_titles([{"title": ""}]) == "(none)"
    many = [{"title": f"t{i}"} for i in range(MAX_TITLES + 5)]
    assert evidence_titles(many).count("\n") == MAX_TITLES - 1


async def test_a3_budget_refusal_is_not_swallowed() -> None:
    from tests.test_copywriter import FakeCompletion as FC  # noqa: F401

    gw, ledger = gateway(FakeCompletion(personas("x")))
    ledger.refusal = BudgetExceeded("run call cap")
    with pytest.raises(BudgetExceeded):
        await PersonaAnalyst(gw, prompt_library()).run({}, agent_ctx(truth_with(V1), step=7))


# ── A7's risk read ───────────────────────────────────────────────────────────────────────────

COPY_STATE = {
    "creative": {"copy": {"headline": "Only 3 left today!", "body": "Everyone is buying."}}
}


async def test_a7_keeps_flags_that_quote_the_copy_and_drops_and_counts_the_rest() -> None:
    gw, ledger = gateway(
        FakeCompletion(
            flags(
                ("false_scarcity", "Only 3 left today!", "urgency with no basis"),
                ("fabricated_social_proof", "10,000 five-star reviews", "not in the copy"),
            )
        )
    )
    out = await RiskReader(gw, prompt_library()).run(COPY_STATE, agent_ctx(truth_with(V1), step=11))
    assert [f.quote for f in out.flags] == ["Only 3 left today!"]
    assert out.dropped_unverifiable == 1
    assert out.source == "model_opinion" and out.advisory_only is True
    (row,) = ledger.rows.values()
    assert row["tier"] == "standard" and "advisory" in row["route_reason"]


async def test_a7_reads_only_the_copy_never_the_products_claims() -> None:
    completion = FakeCompletion(flags())
    gw, _ = gateway(completion)
    await RiskReader(gw, prompt_library()).run(COPY_STATE, agent_ctx(truth_with(V1, R1), step=11))
    prompt = sent(completion)
    assert "Only 3 left today!" in prompt
    assert "Battery lasts 8 hours" not in prompt and "cures anxiety" not in prompt
    assert "c1" not in prompt.replace("claim", "")  # no claim ids either


async def test_a7_makes_no_model_call_when_there_is_no_copy() -> None:
    completion = FakeCompletion()
    gw, _ = gateway(completion)
    out = await RiskReader(gw, prompt_library()).run(
        {"creative": None}, agent_ctx(truth_with(V1), step=11)
    )
    assert out.flags == [] and completion.calls == []


async def test_a7_an_unknown_category_is_invalid_output_not_a_flag() -> None:
    gw, _ = gateway(
        FakeCompletion(
            flags(("bad_vibes", "Only 3 left today!", "x")),
            flags(("false_scarcity", "Only 3 left today!", "x")),
        )
    )
    out = await RiskReader(gw, prompt_library()).run(COPY_STATE, agent_ctx(truth_with(V1), step=11))
    assert [f.category for f in out.flags] == ["false_scarcity"]  # after the gateway's one repair


# ── In the graph: separate from the gate findings, and never a block ─────────────────────────


def graph(*replies: object, truth=None, a7=True):
    completion = FakeCompletion(*replies)
    gw, ledger = gateway(completion)
    prompts = prompt_library()
    agents = {**mock_agents(), "A6": CopyWriter(gw, prompts)}
    if a7:
        agents["A7"] = RiskReader(gw, prompts)
    h = Harness(
        truth=truth or truth_with(V1), agents=agents, extractor=classifier_extractor(gw, prompts)
    )
    return h, completion, ledger


async def test_flags_never_change_the_gate_findings_or_the_outcome_and_sit_apart_from_them() -> (
    None
):
    copy_reply = draft(headline="Only 3 left today!", body="Battery lasts 8 hours.")
    extraction = statements(("Battery lasts 8 hours", "c1"))
    clean, _, _ = graph(copy_reply, extraction, flags())
    flagged, _, _ = graph(
        copy_reply,
        extraction,
        flags(("false_scarcity", "Only 3 left today!", "urgency with no basis")),
    )
    a = (await clean.start()).state["validation"]
    b = (await flagged.start()).state["validation"]

    assert b["advisory"]["flags"][0]["category"] == "false_scarcity"
    assert b["advisory"]["source"] == "model_opinion" and b["advisory"]["advisory_only"] is True
    for key in ("outcome", "findings", "statements", "rules_applied", "sector"):
        assert a[key] == b[key], f"a model opinion changed the gate result: {key}"
    assert b["outcome"] == "pass"
    assert not any("false_scarcity" in json.dumps(f) for f in b["findings"])
    assert a["advisory"]["flags"] == []


async def test_a7_failing_is_a_declared_gap_not_a_block() -> None:
    h, _, _ = graph(draft(), statements(("Battery lasts 8 hours", "c1")), RuntimeError("down"))
    out = await h.start()
    assert (
        out.state["validation"]["outcome"] == "pass" and out.state["validation"]["advisory"] is None
    )
    assert [(e["agent"], e["step"]) for e in out.state["errors"]] == [("A7", 11)]


async def test_a7_budget_refusal_ends_the_run_partial() -> None:
    h, _, ledger = graph(draft(), statements(("Battery lasts 8 hours", "c1")))

    async def refuse(*a: Any, **k: Any) -> Any:
        raise BudgetExceeded("run call cap")

    h.deps.agents = {**h.deps.agents, "A7": type("R", (), {"id": "A7", "run": refuse})()}
    out = await h.start()
    assert out.status == "partial"


async def test_classifier_and_risk_read_share_one_execution_of_step_11() -> None:
    h, _, ledger = graph(draft(), statements(("Battery lasts 8 hours", "c1")), flags())
    await h.start()
    step11 = [c for c in ledger.contexts if c.step == 11]
    assert [c.agent_id for c in step11] == ["A7", "A7"]
    assert step11[0].execution_id == step11[1].execution_id


# ── A3 in the graph feeds A6 ─────────────────────────────────────────────────────────────────


async def test_the_personas_reach_the_copy_writer_as_its_audience() -> None:
    gw_completion = FakeCompletion(
        personas("Commuters, 22-30, find gear on video, objection: durability"),
        draft(),
        statements(("Battery lasts 8 hours", "c1")),
        flags(),
    )
    gw, ledger = gateway(gw_completion)
    prompts = prompt_library()
    agents = {
        **mock_agents(),
        "A3": PersonaAnalyst(gw, prompts),
        "A6": CopyWriter(gw, prompts),
        "A7": RiskReader(gw, prompts),
    }
    h = Harness(agents=agents, extractor=classifier_extractor(gw, prompts))
    out = await h.start()
    assert [p["summary"] for p in out.state["personas"]] == [
        "Commuters, 22-30, find gear on video, objection: durability"
    ]
    a6_prompt = sent(gw_completion, 1)
    assert "Commuters, 22-30, find gear on video, objection: durability" in a6_prompt
    assert [(c.agent_id, c.step) for c in ledger.contexts] == [
        ("A3", 7),
        ("A6", 10),
        ("A7", 11),
        ("A7", 11),
    ]
