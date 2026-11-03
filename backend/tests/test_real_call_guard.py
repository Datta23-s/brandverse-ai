"""The suite cannot reach a real model provider or load the real encoder (owner, 3 November 2026).

The guard lives in tests/conftest.py. It is shown to REFUSE here, in a subprocess that is handed a
provider key on purpose, so a guard that silently did nothing would fail these tests. The sets of
tests allowed to opt out are pinned: they are empty, and widening them is a decision.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests import conftest

BACKEND = Path(__file__).resolve().parents[1]


def run_pytest(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GOOGLE_API_KEY": "planted-by-the-guard-test"}
    return subprocess.run(  # noqa: S603 - fixed command
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=120", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_with_a_provider_key_in_the_environment_every_real_path_is_refused() -> None:
    r = run_pytest("tests/guard_cases.py")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert "4 passed" in r.stdout


def test_a_marker_without_a_listing_fails_collection() -> None:
    r = run_pytest("tests/guard_unlisted_marker_case.py")
    assert r.returncode != 0
    assert "not listed in tests/conftest.py" in r.stdout + r.stderr


def test_the_allowed_sets_are_pinned_and_a_listing_needs_both_marker_and_name() -> None:
    """Widening either set is a decision: it is named here, with a reason in tests/conftest.py."""
    assert conftest.REAL_PROVIDER_TESTS == frozenset(
        {
            "tests/test_llm_request_body.py::"
            "test_every_structured_schema_is_sent_in_the_provider_safe_form"
        }
    )
    assert conftest.REAL_ENCODER_TESTS == frozenset()
    allowed = frozenset({"t::a"})
    assert conftest.real_call_allowed({"real_provider"}, "real_provider", allowed, "t::a")
    assert not conftest.real_call_allowed(set(), "real_provider", allowed, "t::a")  # no marker
    assert not conftest.real_call_allowed({"real_provider"}, "real_provider", allowed, "t::b")


def test_litellm_is_told_not_to_read_the_dotenv_file() -> None:
    code = "import os, app.llm._litellm; print(os.environ.get('LITELLM_MODE'))"
    r = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.stdout.strip().splitlines()[-1] == "PRODUCTION", r.stdout + r.stderr
