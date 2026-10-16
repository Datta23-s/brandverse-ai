"""Database pools are sized from Settings, within the budget, and hold nothing when idle."""

import pytest

from app.core.db import create_db_pool
from tests.conftest import make_settings

URL = "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"


@pytest.mark.parametrize(("role", "size"), [("api", 2), ("worker", 2)])
def test_pool_sized_from_settings(role: str, size: int) -> None:
    pool = create_db_pool(make_settings(database_url=URL), role)  # type: ignore[arg-type]
    assert pool.max_size == size
    assert pool.min_size == 0  # an idle process holds no server connection
    assert pool.name == f"brandverse-{role}"


def test_all_pools_together_stay_within_budget() -> None:
    s = make_settings(database_url=URL)
    opened = create_db_pool(s, "api").max_size + create_db_pool(s, "worker").max_size
    assert opened + s.db_pool_checkpointer_reserved <= s.db_connection_budget == 5


def test_pool_is_created_unopened() -> None:
    # Creating a pool must not connect: the caller opens it inside its event loop.
    pool = create_db_pool(make_settings(database_url=URL), "api")
    assert pool.closed


def test_no_pool_without_database_url() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        create_db_pool(make_settings(), "api")
