"""The Research Necessity Engine as built (decision-log 2.35): the pure decision, the step-5 node
under both depth policies, and the refusal to pretend. The frozen parameters themselves are pinned
by tests/test_necessity_frozen.py; here they are read, never restated."""

from typing import Any

import pytest

from app.memory.necessity import decide, novelty_against, request_text
from app.memory.scoring import load_scoring
from app.orchestrator.deps import GraphDeps, MemoryResult, no_memory
from app.orchestrator.nodes import Nodes, PipelineInputError

CFG = load_scoring().necessity
pytestmark = pytest.mark.anyio


def test_a_cold_start_is_d2_and_a_full_memory_with_a_familiar_request_is_d0() -> None:
    cold = decide(
        CFG, novelty=1.0, similarities=[], regulated=False, requested="D1", policy="necessity"
    )
    assert (cold.u, cold.depth, cold.advised) == (1.0, "D2", "D2")
    full = decide(
        CFG,
        novelty=0.05,
        similarities=[0.9] * CFG.item_cap,
        regulated=False,
        requested="D2",
        policy="necessity",
    )
    assert (full.u, full.depth) == (0.0, "D0")


def test_the_second_identical_request_picks_a_lower_depth() -> None:
    """The exit criterion in Phases.md: same request twice, the second runs lower."""
    first = decide(
        CFG, novelty=1.0, similarities=[], regulated=False, requested="D1", policy="necessity"
    )
    second = decide(
        CFG,
        novelty=0.0,
        similarities=[0.8] * 8,
        regulated=False,
        requested="D1",
        policy="necessity",
    )
    assert ["D0", "D1", "D2"].index(second.depth) < ["D0", "D1", "D2"].index(first.depth)


def test_only_items_at_the_floor_count_and_the_count_saturates_at_the_cap() -> None:
    floor = CFG.relevance_floor
    d = decide(
        CFG,
        novelty=0.2,
        similarities=[floor, floor - 0.01, 0.99] + [0.9] * 40,
        regulated=False,
        requested="D1",
        policy="necessity",
    )
    assert d.n_relevant == CFG.item_cap and d.u == 0.0 and d.n_recalled == 43


def test_a_regulated_sector_raises_r() -> None:
    kw: dict[str, Any] = {"novelty": 0.2, "similarities": [0.9] * 6, "requested": "D1"}
    plain = decide(CFG, regulated=False, policy="necessity", **kw)
    regulated = decide(CFG, regulated=True, policy="necessity", **kw)
    assert regulated.r > plain.r and regulated.s_sector == 1.0


def test_under_the_requested_policy_the_depth_is_kept_and_the_advice_is_recorded() -> None:
    d = decide(
        CFG, novelty=1.0, similarities=[], regulated=False, requested="D0", policy="requested"
    )
    assert (d.depth, d.advised) == ("D0", "D2")
    rec = d.record()
    assert rec["policy"] == "requested" and rec["advised"] == "D2" and rec["depth"] == "D0"


def test_novelty_is_one_minus_the_largest_cosine_and_one_with_no_prior() -> None:
    assert novelty_against([1.0, 0.0], []) == 1.0
    assert novelty_against([1.0, 0.0], [[0.0, 1.0], [0.8, 0.6]]) == pytest.approx(0.2)
    assert novelty_against([1.0, 0.0], [[1.0, 0.0]]) == 0.0
    assert novelty_against([1.0, 0.0], [[-1.0, 0.0]]) == 1.0  # clamped


def test_the_request_text_is_the_form_the_stage2_measurement_used() -> None:
    assert request_text("shoes", "d2c", "Monsoon", "runners") == "shoes. d2c. Monsoon runners"


class _Events:
    async def append(self, event: object) -> str:
        return "1"


def _nodes(**over: Any) -> Nodes:
    async def unused(*a: Any, **k: Any) -> Any:
        raise AssertionError("not used by step 5")

    deps = GraphDeps(
        events=_Events(),
        load_context=unused,
        retrieve=no_memory,
        research=unused,
        agents={},
        write_memory=unused,
        extract_statements=unused,
        lexicon=None,  # type: ignore[arg-type]
        today=lambda: None,  # type: ignore[arg-type,return-value]
        **over,
    )
    return Nodes(deps)


async def test_step5_chooses_the_depth_under_the_necessity_policy() -> None:
    async def novelty(_state: Any) -> float:
        return 1.0

    out = await _nodes(necessity=CFG, novelty=novelty).assess_research_necessity(
        {"depth": "D0", "depth_policy": "necessity", "memory_similarities": []}
    )
    assert out["depth"] == "D2" and out["necessity"]["requested"] == "D0"
    assert out["research_score"] == out["necessity"]["r"]


async def test_step5_keeps_the_requested_depth_and_records_the_advice() -> None:
    out = await _nodes(necessity=CFG).assess_research_necessity(
        {"depth": "D1", "memory_similarities": []}
    )
    assert out["depth"] == "D1" and out["necessity"]["advised"] == "D2"


async def test_a_pipeline_without_the_engine_runs_the_requested_depth_and_says_so() -> None:
    out = await _nodes().assess_research_necessity({"depth": "D2"})
    assert out["depth"] == "D2" and "no necessity engine" in out["_detail"]


async def test_asking_for_the_engine_without_one_fails_instead_of_quietly_using_the_request() -> (
    None
):
    state: Any = {"depth": "D1", "depth_policy": "necessity"}
    with pytest.raises(PipelineInputError, match="no necessity engine"):
        await _nodes().assess_research_necessity(state)
    with pytest.raises(PipelineInputError, match="no source"):
        await _nodes(necessity=CFG).assess_research_necessity(state)


def test_the_recalled_similarities_travel_in_the_memory_result() -> None:
    assert MemoryResult([], 0.0, 0, 0, [0.7, 0.6]).similarities == [0.7, 0.6]
    assert MemoryResult([], 0.0).similarities == []
