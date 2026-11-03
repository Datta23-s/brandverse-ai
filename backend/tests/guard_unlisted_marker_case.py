"""A marked test that is not in tests/conftest.py's set: collection must fail (run by
tests/test_real_call_guard.py; not collected by the normal run)."""

import pytest


@pytest.mark.real_provider
def test_marked_but_not_listed() -> None:
    raise AssertionError("must never run")
