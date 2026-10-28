"""Settings: loaded once, connection budget enforced, session pooler only (Architecture.md §11)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.conftest import make_settings

SESSION_POOLER = (
    "postgresql://postgres.ref:s3cret-pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
)


def test_defaults_fill_the_budget_exactly() -> None:
    s = make_settings()
    total = s.db_pool_api_max_size + s.db_pool_worker_max_size + s.db_pool_checkpointer_reserved
    assert s.db_connection_budget == 5
    assert total == 5


def test_pools_over_budget_are_refused() -> None:
    with pytest.raises(ValidationError, match="over the budget of 5"):
        make_settings(db_pool_api_max_size=3)


def test_checkpointer_share_counts_toward_budget() -> None:
    with pytest.raises(ValidationError, match="checkpointer 2"):
        make_settings(db_pool_checkpointer_reserved=2)


def test_session_pooler_url_accepted_and_whitespace_trimmed() -> None:
    s = make_settings(database_url=SESSION_POOLER + "\t\t")
    assert s.database_url is not None
    assert s.database_url.get_secret_value() == SESSION_POOLER


def test_transaction_pooler_refused() -> None:
    with pytest.raises(ValidationError, match="transaction pooler"):
        make_settings(database_url=SESSION_POOLER.replace(":5432/", ":6543/"))


@pytest.mark.parametrize(
    "bad",
    [
        # The doubled scheme found in the laptop's .env on 2026-10-16.
        "postgresql:" + SESSION_POOLER,
        "mysql://u:s3cret-pw@host/db",
        "not a url",
    ],
)
def test_malformed_url_refused_without_echoing_the_password(bad: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_settings(database_url=bad)
    assert "s3cret-pw" not in str(excinfo.value)


def test_database_url_is_never_rendered() -> None:
    s = make_settings(database_url=SESSION_POOLER)
    assert "s3cret-pw" not in repr(s)
    assert "s3cret-pw" not in str(s.model_dump())


def test_empty_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships `REDIS_URL=` with no value.
    monkeypatch.setenv("REDIS_URL", "")
    assert str(make_settings().redis_url) == "redis://127.0.0.1:6379/0"


def test_environment_variables_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6390/3")
    monkeypatch.setenv("WORKER_MAX_JOBS", "1")
    s = make_settings()
    assert str(s.redis_url) == "redis://localhost:6390/3"
    assert s.worker_max_jobs == 1


def test_fake_runs_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="fake_runs_enabled"):
        make_settings(environment="production")
    assert (
        make_settings(environment="production", fake_runs_enabled=False).fake_runs_enabled is False
    )


def test_settings_are_loaded_once() -> None:
    assert get_settings() is get_settings()
    assert isinstance(get_settings(), Settings)


def test_pending_expiry_must_exceed_the_longest_live_call() -> None:
    # (3 waits + 1) x 60 s timeout + 90 s of waiting = 330 s at the defaults.
    with pytest.raises(ValidationError, match="longest a call can stay pending"):
        make_settings(llm_pending_expiry_s=330)
    assert make_settings().llm_pending_expiry_s == 900


def test_the_strong_tier_is_off_by_default() -> None:
    assert make_settings().llm_allow_strong_tier is False
    assert make_settings().llm_strong_tier_org_id is None  # and unscoped
