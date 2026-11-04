"""The two diagnostics added after the stage-2 interim hang (2026-11-04) are off by default."""

import pytest

from app.core.config import Settings


def test_the_diagnostics_are_off_by_default() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.research_step_timeout_s is None
    assert s.worker_torch_threads is None


def test_the_stage2_worker_environment_leaves_the_thread_caps_off() -> None:
    """Owner, 2026-11-04: the caps stay in config, disabled, until a cause is established."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "phase7_stage2.py").read_text(
        encoding="utf-8"
    )
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "WORKER_TORCH_THREADS"):
        assert f'"{name}":' not in src


def test_the_stage2_worker_logs_unbuffered_and_has_a_job_timeout_that_fits_d2() -> None:
    """2026-11-04: a redirected stdout is block-buffered, so a terminated worker lost its last
    lines; and ARQ's 600 s default killed a D2 run whose research alone takes 205 to 313 s."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "phase7_stage2.py").read_text(
        encoding="utf-8"
    )
    assert '"PYTHONUNBUFFERED": "1"' in src
    assert '"WORKER_JOB_TIMEOUT_S": "1800"' in src
    assert "update runs set status = 'failed'" in src  # stale runs are not resumed by the sweep


def test_the_environment_turns_the_research_timeout_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_STEP_TIMEOUT_S", "420")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.research_step_timeout_s == 420.0


def test_the_stage2_worker_has_the_research_timeout() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "phase7_stage2.py").read_text(
        encoding="utf-8"
    )
    assert '"RESEARCH_STEP_TIMEOUT_S"' in src


def test_nothing_in_the_code_arms_a_repeating_stack_dump() -> None:
    """`faulthandler.dump_traceback_later` reads other threads' frames without the GIL. On
    2026-11-04 it killed this project's worker and three replay scripts mid-dump (each log shows
    `File ` cut off by `Windows fatal exception: access violation`), and for hours that was
    mistaken for a crash in lxml, in the regex engine and in the interpreter.
    `faulthandler.enable()` (a stack at a fatal signal) is safe and stays."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    offenders = [
        str(path.relative_to(backend))
        for folder in ("app", "scripts")
        for path in (backend / folder).rglob("*.py")
        if "dump_traceback_later(" in path.read_text(encoding="utf-8")
        and path.name != "test_diagnostic_settings.py"
    ]
    assert offenders == []
