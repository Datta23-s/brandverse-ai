"""The evaluation arms (decision-log 2.48): the per-arm plumbing, Baseline A's single call, and
the REGRESSION PROOF that the Proposed arm is unchanged by it.

The proof the owner required is not an assertion: the COUNT live run's recorded inputs
(evaluation/regression/count_live_run_20261106.json, read from the run checkpoints of 6 November)
are replayed through the pipeline as it is now, and every depth decision and every injected item
must come out identical.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.baseline import BaselineWriter
from app.memory.injection import InjectionPolicy
from app.memory.scoring import load_scoring
from app.orchestrator.deps import MemoryResult
from app.orchestrator.state import RunState
from tests.llm_fakes import FakeCompletion, prompt_library
from tests.test_copywriter import agent_ctx, draft, gateway, sent
from tests.test_orchestrator_graph import VERIFIED, Harness, truth_with

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).resolve().parents[2] / "evaluation" / "regression"
SCORING = load_scoring()


def engine(h: Harness, novelty: float) -> None:
    async def n(state: RunState) -> float:
        return novelty

    h.deps.necessity, h.deps.novelty = SCORING.necessity, n
    h.deps.injection = InjectionPolicy(SCORING.memory.bands, SCORING.necessity.item_cap)


# ── The regression proof: replay the COUNT live run ──────────────────────────────────────────


async def test_the_proposed_arm_makes_the_same_depth_and_injection_decisions_as_the_live_run() -> (
    None
):
    live = json.loads((FIXTURE / "count_live_run_20261106.json").read_text(encoding="utf-8"))
    assert len(live["campaigns"]) == 3
    for c in live["campaigns"]:
        h = Harness()
        engine(h, c["novelty"])

        async def retrieve(state: Any, personas: Any = None, c: dict[str, Any] = c) -> MemoryResult:
            # step 4 (no persona) feeds U with the recalled similarities; step 8 re-scores
            return MemoryResult(c["memory_hits"], 0.0, 0, 2, c["memory_similarities"])

        h.deps.retrieve = retrieve
        out = await h.start(depth=c["requested_depth"], depth_policy="necessity")
        want = c["expected"]
        s = out.state
        assert s["depth"] == want["depth"], c["campaign"]
        assert s["necessity"]["r"] == pytest.approx(want["r"], abs=1e-4)
        assert s["necessity"]["u"] == pytest.approx(want["u"], abs=1e-4)
        assert s["necessity"]["n_relevant"] == want["n_relevant"]
        got = [
            (i["id"], i["band"], pytest.approx(i["m"], abs=1e-4)) for i in s["injection"]["items"]
        ]
        assert got == [
            (i["id"], i["band"], pytest.approx(i["m"], abs=1e-4)) for i in want["injection"]
        ]
        assert (s["injection"]["over_cap"], s["injection"]["below_floor"]) == (
            want["over_cap"],
            want["below_floor"],
        )
        assert s["strategy"]["injection"] == s["injection"] and s["arm"] == "proposed"


# ── Baseline B: all agents, D2 always, no memory ─────────────────────────────────────────────


async def recalling_harness(arm_novelty: float = 0.1) -> tuple[Harness, list[str]]:
    h = Harness()
    engine(h, arm_novelty)
    calls: list[str] = []

    async def retrieve(state: Any, personas: Any = None) -> MemoryResult:
        calls.append("retrieve")
        hit = {"id": "m1", "kind": "insight", "content": "a note", "m": 0.95, "band": "reuse"}
        return MemoryResult([hit], 1.0, 2, 2, [0.9] * 12)

    h.deps.retrieve = retrieve
    return h, calls


async def test_baseline_b_is_d2_with_all_agents_and_neither_reads_nor_writes_memory() -> None:
    h, calls = await recalling_harness()
    # asks for the engine and for D0: the ARM decides, not the request
    out = await h.start(depth="D0", depth_policy="necessity", arm="baseline_b")
    s = out.state
    assert s["depth"] == "D2" and s["necessity"]["policy"] == "baseline_b"
    assert calls == []  # memory is never recalled, at step 4 or after the personas
    assert s["memory_hits"] == [] and s["injection"] is None
    assert sorted(s["agent_outputs"]) == ["A1", "A2", "A3"]  # all built agents, A2 included
    assert s["research"] == {"depth": "D2"}
    await h.decide("approve")
    assert h.memory_calls == [] or all(c["decision"] == "approve" for c in h.memory_calls)
    assert not h.memory_calls  # the write-back step is skipped: nothing proposed or written


# ── Baseline A: one call, no research, no agents, no memory ──────────────────────────────────


async def test_baseline_a_is_a_single_writer_with_no_research_agents_memory_or_advisory() -> None:
    h, calls = await recalling_harness()
    out = await h.start(depth="D2", arm="baseline_a")
    s = out.state
    steps = h.sink.steps()
    assert (6, "skipped") in steps and (6, "started") not in steps  # no research, not even D0's
    assert s["selected_agents"] == ["BASELINE_A"] and s["agent_outputs"] == {}
    assert s["creative"]["copy"]["headline"].startswith("Battery lasts 8 hours")
    assert s["validation"]["advisory"] is None  # no A7 on this arm
    assert calls == [] and s["injection"] is None and s["findings"] == []
    done = await h.decide("approve")
    assert done.status == "completed" and h.memory_calls == []


async def test_a_proposed_run_is_still_the_default_and_still_recalls_memory() -> None:
    h, calls = await recalling_harness()
    out = await h.start(depth="D1")
    assert out.state["arm"] == "proposed" and calls  # recalled at step 4 (and again at step 8)
    assert out.state["injection"]["items"][0]["band"] == "reuse"


# ── Baseline A's prompt: what it receives, and effort parity ─────────────────────────────────


async def test_baseline_a_receives_the_brief_audience_claims_and_voice_and_nothing_else() -> None:
    completion = FakeCompletion(draft())
    gw, ledger = gateway(completion)
    state = {
        "brief": "Monsoon launch",
        "audience": "daily commuters",
        "product": {"voice": "Warm, plain-spoken, a little dry."},
        # everything below is the agents' product and must NOT reach the prompt
        "strategy": {"positioning": "POSITIONING-SECRET"},
        "personas": [{"summary": "PERSONA-SECRET"}],
        "agent_outputs": {"A2": {"for_copy": ["MARKET-SECRET"]}},
        "injection": {"items": [{"band": "reuse", "text": "MEMORY-SECRET"}]},
        "findings": [{"url": "https://x.example/", "title": "FINDING-SECRET"}],
    }
    out = await BaselineWriter(gw, prompt_library()).run(state, agent_ctx(truth_with(VERIFIED)))
    prompt = sent(completion)
    assert "Monsoon launch" in prompt and "daily commuters" in prompt
    assert "Warm, plain-spoken, a little dry." in prompt and "Battery lasts 8 hours" in prompt
    for secret in ("POSITIONING", "PERSONA", "MARKET", "MEMORY", "FINDING"):
        assert f"{secret}-SECRET" not in prompt
    (row,) = ledger.rows.values()
    assert (row["prompt"].prompt_id, row["prompt"].prompt_version) == ("baseline_a_copy", "v1")
    assert row["tier"] == "standard" and out.copy_["headline"]


async def test_baseline_a_with_no_voice_says_none_given_and_the_calls_are_one() -> None:
    completion = FakeCompletion(draft())
    gw, ledger = gateway(completion)
    await BaselineWriter(gw, prompt_library()).run({}, agent_ctx(truth_with(VERIFIED)))
    assert "Brand voice:\n(none given)" in sent(completion)
    assert len(completion.calls) == 1  # one model call


def test_baseline_a_carries_a6s_rules_word_for_word_effort_parity() -> None:
    """The constraint the owner set: the baseline's prompt is not under-specified. Every rule of
    a6_copy@v3 is in baseline_a_copy@v1 verbatim, except exactly the three that describe inputs the
    baseline does not receive (the category notes, the remembered notes) or are reworded because
    their referent is absent (the positioning's voice)."""
    lib = prompt_library()
    a6 = lib.get("a6_copy", "v3").system.splitlines()
    base = lib.get("baseline_a_copy", "v1").system.splitlines()
    rules6 = [x for x in a6 if x.startswith("  - ") or x.startswith("- ")]
    rulesb = [x for x in base if x.startswith("  - ") or x.startswith("- ")]
    absent = [r for r in rules6 if r not in rulesb]
    assert len(absent) == 3, absent
    text = " ".join(absent)
    assert "category notes" in text.lower() and "remembered notes" in text.lower()
    assert "positioning describes" in text  # the one reworded rule
    # and nothing was ADDED that A6 does not get
    added = [r for r in rulesb if r not in rules6]
    assert len(added) == 1 and "brand's voice where one is given" in added[0]
    # the opening, the closing format line and the revision rule are identical
    assert a6[0] == base[0] and a6[-1] == base[-1]
