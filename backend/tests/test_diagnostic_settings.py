"""The two diagnostics added after the stage-2 interim hang (2026-11-04) are off by default."""

import pytest

from app.core.config import Settings


def test_both_diagnostics_are_off_by_default() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.worker_stack_dump_s is None
    assert s.research_step_timeout_s is None
    assert s.worker_torch_threads is None


def test_the_stage2_worker_environment_caps_the_math_threads() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "phase7_stage2.py").read_text(
        encoding="utf-8"
    )
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "WORKER_TORCH_THREADS"):
        assert f'"{name}": "1"' in src


def test_the_environment_turns_them_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_STACK_DUMP_S", "120")
    monkeypatch.setenv("RESEARCH_STEP_TIMEOUT_S", "420")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.worker_stack_dump_s == 120.0
    assert s.research_step_timeout_s == 420.0


def test_a_dump_interval_under_ten_seconds_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_STACK_DUMP_S", "1")
    with pytest.raises(ValueError, match="worker_stack_dump_s"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_stage2_worker_is_started_with_both() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "phase7_stage2.py").read_text(
        encoding="utf-8"
    )
    assert '"WORKER_STACK_DUMP_S"' in src and '"RESEARCH_STEP_TIMEOUT_S"' in src
