"""The generation run's real I/O (decision-log 2.50): the parts that need no database.

The database half (seeding, finding a run by what it is, reading decisions) is exercised by the dry
run against the throwaway database; this file pins the decisions that do not need one.
"""

from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.evaluation.brief_checks import FrozenBriefChecksError, for_brief
from app.evaluation.generation_runner import (
    ASKED_DEPTH,
    EVALUATION_DATABASES,
    classify,
    describe_target,
    target_refusal,
)
from app.evaluation.test_set import request_index, request_inputs
from app.llm.evaluation_budget import EVALUATION_BUDGET_PATH
from app.orchestrator.runner import PipelineOutcome
from app.workers.gate import ExecutionGate
from app.workers.jobs import run_pipeline

# ── what a run's row means to the driver ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "failure", "expected"),
    [
        ("awaiting_approval", None, ("parked", None)),
        ("queued", None, ("running", None)),
        ("completed", None, ("ended", None)),
        ("blocked", None, ("ended", None)),  # a gate block is a RESULT
        ("partial", None, ("ended", None)),  # a budget stop is a result
        ("failed", None, ("ended", None)),  # a failure with no infrastructure kind is a result
        ("failed", {"kind": "x"}, ("ended", None)),
        ("failed", {"infrastructure_failure": "provider_unavailable"}, ("invalidated", "provider_unavailable")),  # noqa: E501
        ("failed", {"kind": "job_timeout", "infrastructure_failure": "process_crash"}, ("invalidated", "process_crash")),  # noqa: E501
    ],
)  # fmt: skip
def test_a_runs_status_is_one_driver_outcome(
    status: str, failure: dict[str, Any] | None, expected: tuple[str, str | None]
) -> None:
    assert classify(status, failure) == expected


def test_an_unknown_status_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="unknown run status"):
        classify("stuck", None)


# ── the target ───────────────────────────────────────────────────────────────────────────────

THROWAWAY = "brandverse_dryrun_eval"


@pytest.mark.parametrize(
    ("url", "refused"),
    [
        (f"postgresql://postgres:postgres@127.0.0.1:55432/{THROWAWAY}", False),
        (f"postgresql://postgres:postgres@localhost:55432/{THROWAWAY}", False),
        ("postgresql://postgres:postgres@127.0.0.1:55432/brandverse_evaluation", True),
        ("postgresql://postgres:postgres@127.0.0.1:55432/postgres", True),
        ("postgresql://postgres:postgres@127.0.0.1:55432/brandverse_stage2", True),
        (f"postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/{THROWAWAY}", True),
        (f"postgresql://postgres:pw@10.0.0.7:5432/{THROWAWAY}", True),
    ],
)
def test_the_dry_run_touches_only_the_local_throwaway(url: str, refused: bool) -> None:
    assert (target_refusal(url, THROWAWAY) is not None) is refused


def test_the_target_is_described_the_way_it_is_printed() -> None:
    assert describe_target(f"postgresql://u:p@127.0.0.1:55432/{THROWAWAY}") == (
        "127.0.0.1",
        55432,
        THROWAWAY,
    )


def test_the_real_evaluation_database_is_the_one_the_check_names() -> None:
    import yaml

    registered = set(
        yaml.safe_load(EVALUATION_BUDGET_PATH.read_text(encoding="utf-8"))["databases"]
    )
    assert registered <= EVALUATION_DATABASES  # a database added to the real budget is also guarded
    assert THROWAWAY not in registered  # and the dry run never registers itself in the real file


def test_every_arm_has_an_asked_depth() -> None:
    assert set(ASKED_DEPTH) == {"proposed", "baseline_a", "baseline_b"}


# ── the design's shape, as the Runner reads it ───────────────────────────────────────────────


def test_the_index_matches_the_design() -> None:
    idx = request_index()
    assert len(idx) == 30
    assert idx["R01"].brand_id == "B01" and idx["R01"].position == 1
    assert idx["R03"].product_key == idx["R01"].product_key  # campaign 3 is the first product again
    assert idx["R02"].product_key != idx["R01"].product_key
    assert set(request_inputs()) == set(idx)


# ── the frozen brief check reaches the run (found before the dry run) ────────────────────────


def test_a_frozen_brief_check_is_found_by_the_hash_of_its_brief() -> None:
    brief = request_inputs()["R01"].brief
    record = for_brief(brief)
    assert record["status"] == "checked"
    assert for_brief(brief) == record  # the same record every time, for every arm


def test_a_brief_nobody_froze_a_check_for_is_refused_not_checked_ad_hoc() -> None:
    with pytest.raises(FrozenBriefChecksError):
        for_brief("A brief that is not in the test set.")


class _Recorder:
    def __init__(self) -> None:
        self.kw: dict[str, Any] = {}

    async def start(self, **kw: Any) -> PipelineOutcome:
        self.kw = kw
        return PipelineOutcome("completed", {})


def _payload(brief: str) -> dict[str, Any]:
    return {
        "run_id": str(uuid4()),
        "request": {"org_id": str(uuid4()), "product_id": str(uuid4()), "brief": brief},
        "by": str(uuid4()),
    }


def _ctx(p: _Recorder, *, evaluation: bool) -> dict[str, Any]:
    return {
        "pipeline": p,
        "gate": ExecutionGate(),
        "settings": Settings(  # type: ignore[call-arg]
            _env_file=None,
            evaluation_run=evaluation,
            # mock agents: the start-up refusals (timeout, snapshot) apply to real ones; this test
            # is about what the JOB passes to the pipeline.
            pipeline_agents="mock",
        ),
    }


@pytest.mark.anyio
async def test_an_evaluation_worker_starts_every_run_with_the_frozen_brief_check() -> None:
    p = _Recorder()
    brief = request_inputs()["R01"].brief
    await run_pipeline(_ctx(p, evaluation=True), _payload(brief))
    assert p.kw["brief_check"] == for_brief(brief)


@pytest.mark.anyio
async def test_an_ordinary_worker_still_computes_its_own() -> None:
    p = _Recorder()
    await run_pipeline(_ctx(p, evaluation=False), _payload("Any brief at all."))
    assert p.kw["brief_check"] is None


@pytest.mark.anyio
async def test_an_evaluation_worker_refuses_a_brief_that_is_not_the_test_sets() -> None:
    with pytest.raises(FrozenBriefChecksError):
        await run_pipeline(_ctx(_Recorder(), evaluation=True), _payload("Not in the test set."))


# ── the budget scope reaches the worker's ledger (found before the dry run) ──────────────────


def test_the_worker_hands_the_evaluation_budget_to_its_ledger() -> None:
    """Pinned at the source: `open_pipeline` builds a `CallLedger` and, until 2.50, passed only the
    development budget, so every call from an evaluation database was refused. The live proof is the
    dry run's ledger (no refused calls); this is the line that must not be deleted."""
    from pathlib import Path

    import app.workers.pipeline_factory as factory

    text = Path(factory.__file__).read_text(encoding="utf-8")
    assert "evaluation_budget=load_evaluation_budget(settings.evaluation_budget_path)" in text
    assert Settings(_env_file=None).evaluation_budget_path == EVALUATION_BUDGET_PATH  # type: ignore[call-arg]
