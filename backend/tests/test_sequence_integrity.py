"""A Proposed campaign that wrote no memory BLOCKS the next round (owner, 7 November 2026).

Two properties:
1. the rule itself, case by case, including the one case that writes nothing and is CORRECT;
2. that it AGREES with the rule the stage-2 runner already enforced. `scripts/phase7_stage2.py` is
   a registered file under the freeze tag and is not edited to import the shared module, so the
   guarantee that the evaluation reuses the same rule is this test, which drives both over the same
   table and fails on any disagreement.
"""

from typing import Any

import pytest

from app.evaluation.generation import Checkpoint, GenerationStopped, generate, wave_items
from app.evaluation.schedule import registered_order
from app.evaluation.schedule import test_set_requests as request_rows  # not a test: pytest collects

# anything named test_*
from app.evaluation.sequence import campaign_problem
from scripts.phase7_stage2 import campaign_problem as stage2_campaign_problem
from tests.generation_fakes import FileRunner

GAP = [{"step": 7, "agent": "A2", "error": "LLMCallFailed", "infrastructure": "network_error"}]

# (status, gaps, memory written, depth, engine chose the depth) -> is it a problem?
CASES: list[tuple[str | None, list[dict[str, Any]], int, str | None, bool, bool]] = [
    ("completed", [], 5, "D2", True, False),  # the normal case
    ("completed", [], 0, "D0", True, False),  # D0 by the ENGINE: no memory BY DESIGN
    ("completed", [], 0, "D2", True, True),  # wrote nothing at a depth that should have
    ("completed", [], 0, "D1", True, True),
    ("completed", GAP, 0, "D2", True, True),  # an agent failed
    ("completed", GAP, 5, "D2", True, True),  # an agent failed even though something was written
    ("partial", [], 5, "D2", True, True),  # a budget stop is not a campaign the next round reads
    ("blocked", [], 0, "D2", True, True),
    ("failed", [], 0, "D2", True, True),
    (None, [], 0, "D2", True, True),
    ("completed", [], 0, "D0", False, True),  # D0 the REQUEST asked for: memory was still expected
]


@pytest.mark.parametrize(("status", "gaps", "written", "depth", "engine", "is_problem"), CASES)
def test_the_rule_case_by_case(
    status: str | None,
    gaps: list[dict[str, Any]],
    written: int,
    depth: str | None,
    engine: bool,
    is_problem: bool,
) -> None:
    problem = campaign_problem(
        status, gaps, memory_items_written=written, depth=depth, engine_chose_depth=engine
    )
    assert (problem is not None) is is_problem, problem


def test_the_one_case_that_writes_nothing_and_passes_is_named_in_the_reason() -> None:
    """A blanket "no memory means broken" rule would reject the engine's own D0 campaign: that is
    the case the evaluation hit on 6 November (decision-log 2.46)."""
    assert (
        campaign_problem(
            "completed", [], memory_items_written=0, depth="D0", engine_chose_depth=True
        )
        is None
    )
    blocked = campaign_problem(
        "completed", [], memory_items_written=0, depth="D1", engine_chose_depth=True
    )
    assert blocked is not None and "cold start" in blocked


@pytest.mark.parametrize(("status", "gaps", "written", "depth", "engine", "_is_problem"), CASES)
def test_it_agrees_with_the_rule_the_stage_2_runner_enforced(
    status: str | None,
    gaps: list[dict[str, Any]],
    written: int,
    depth: str | None,
    engine: bool,
    _is_problem: bool,
) -> None:
    """Stage 2 phrased the same rule as (status, gaps, items before, items after, writes expected).
    Both must reach the same verdict on every case, or the evaluation is not reusing the rule."""
    mine = campaign_problem(
        status, gaps, memory_items_written=written, depth=depth, engine_chose_depth=engine
    )
    theirs = stage2_campaign_problem(
        status,
        gaps,
        0,
        written,
        writes_expected=not (engine and depth == "D0"),
    )
    assert (mine is None) == (theirs is None), f"mine={mine!r} stage2={theirs!r}"


# ── the gate in the driver ───────────────────────────────────────────────────────────────────

pytestmark_anyio = pytest.mark.anyio
POSITIONS = {r["id"]: r["position"] for r in request_rows()}


@pytest.mark.anyio
async def test_an_approved_round_that_wrote_no_memory_still_refuses_the_next_round(
    tmp_path: Any,
) -> None:
    """The point of the ruling: an approval is NOT enough to release the next round."""
    ckpt = Checkpoint(tmp_path / "c.jsonl")
    items = wave_items(registered_order(), POSITIONS, "round:2")
    decided = frozenset({("proposed", 1)})  # every decision is in
    runner = FileRunner(
        tmp_path / "db.json",
        decided_positions=decided,
        sequence_problems_by_position={1: ["R01 (run abc): it wrote no memory (depth D2)"]},
    )
    with pytest.raises(GenerationStopped, match="did not leave the memory"):
        await generate(items, runner, ckpt, "round:2")
    assert runner.started == [] and ckpt.events() == []  # refused before anything was started

    # with the memory in place, the same round runs
    ok = FileRunner(tmp_path / "db.json", decided_positions=decided)
    report = await generate(items, ok, Checkpoint(tmp_path / "c2.jsonl"), "round:2")
    assert len(report.started) == len(items) == len(request_rows()) // 3 * 3
