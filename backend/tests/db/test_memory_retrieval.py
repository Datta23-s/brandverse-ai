"""The caller of app.match_memory sets hnsw.ef_search per transaction and reports the value used
(app/memory/retrieval.py; config/scoring.yaml `retrieval`).

ef_search was first set on the function itself, which the live project refused (a non-superuser
may not attach an unloaded extension's setting to a function). It is a search-time parameter the
evaluation sweeps, so it now belongs to the caller.
"""

import json
import logging
from collections.abc import AsyncIterator

import pytest
from psycopg import AsyncConnection

from app.memory.retrieval import match_memory
from app.memory.scoring import load_scoring
from tests.db.conftest import Seed

pytestmark = [pytest.mark.anyio, pytest.mark.db]

UNIT = "[" + ",".join(["1"] + ["0"] * 767) + "]"
CONFIGURED = load_scoring().retrieval.hnsw_ef_search


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    """A new connection whose transaction is always rolled back."""
    c = await AsyncConnection.connect(db_url)
    try:
        yield c
    finally:
        await c.rollback()
        await c.close()


async def act_as(conn: AsyncConnection, seed: Seed, user: str) -> None:
    claims = {"sub": str(seed.users[user]), "role": "authenticated"}
    await conn.execute("set local role authenticated")
    await conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


async def setting(conn: AsyncConnection) -> str | None:
    row = await (await conn.execute("select current_setting('hnsw.ef_search', true)")).fetchone()
    assert row is not None
    value: str | None = row[0]
    return value


async def test_the_configured_depth_is_applied_and_reported(
    conn: AsyncConnection, seed: Seed
) -> None:
    await conn.execute(
        "insert into memory_items (org_id, brand_id, kind, content, embedding, embedding_model, "
        "embedding_version, provenance, confidence, approved) values (%s, %s, 'insight', "
        "'rain sells', %s::vector, 'test', 'test', 'test', 0.5, true)",
        (seed.org_a, seed.brand_a, UNIT),
    )
    await act_as(conn, seed, "owner_a")
    r = await match_memory(conn, seed.org_a, UNIT, "rain")
    assert r.ef_search == CONFIGURED == 100
    assert [row["content"] for row in r.rows] == ["rain sells"]
    assert await setting(conn) == str(CONFIGURED)


async def test_a_sweep_value_overrides_the_default_and_ends_with_the_transaction(
    conn: AsyncConnection, seed: Seed
) -> None:
    await act_as(conn, seed, "owner_a")
    r = await match_memory(conn, seed.org_a, UNIT, "rain", ef_search=250)
    assert r.ef_search == 250
    await conn.rollback()
    assert await setting(conn) != "250"  # transaction-local: the pooled connection goes back clean


async def test_set_by_the_signed_in_role_before_pgvector_is_loaded(
    conn: AsyncConnection, seed: Seed
) -> None:
    # The live failure: a fresh backend, pgvector's library not yet loaded, a non-superuser role.
    assert await setting(conn) is None, "this backend already has pgvector loaded"
    await act_as(conn, seed, "owner_a")
    r = await match_memory(conn, seed.org_a, UNIT, "anything")
    assert r.ef_search == CONFIGURED
    assert await setting(conn) == str(CONFIGURED)  # carried into the real setting once loaded


async def test_every_retrieval_logs_the_depth_used(
    conn: AsyncConnection, seed: Seed, caplog: pytest.LogCaptureFixture
) -> None:
    await act_as(conn, seed, "owner_a")
    with caplog.at_level(logging.INFO, logger="app.memory.retrieval"):
        await match_memory(conn, seed.org_a, UNIT, "rain", ef_search=64)
    (record,) = [r for r in caplog.records if r.getMessage() == "memory retrieval"]
    assert record.ef_search == 64  # type: ignore[attr-defined]
    assert record.org_id == str(seed.org_a)  # type: ignore[attr-defined]


async def test_refused_outside_a_transaction_and_out_of_range(db_url: str, seed: Seed) -> None:
    async with await AsyncConnection.connect(db_url, autocommit=True) as c:
        with pytest.raises(ValueError, match="autocommit"):
            await match_memory(c, seed.org_a, UNIT, "x")
    async with await AsyncConnection.connect(db_url) as c:
        with pytest.raises(ValueError, match="1..1000"):
            await match_memory(c, seed.org_a, UNIT, "x", ef_search=0)


async def test_every_m_score_field_retrieval_supplies_is_a_column_of_the_returned_row(
    conn: AsyncConnection, seed: Seed
) -> None:
    """The M-score's `Candidate` reads retrieval's rows by field name (the Phase 6 caller builds
    one per row), so a name the function does not return would leave that component silently out
    of M. `source_score` is the one 0031 added: E was always left out before it.

    Two fields are NOT retrieval's to supply and are excluded here: `customer_similarity` (C, from
    persona vectors, Phase 6) and `id`, which the row has under the same name anyway.
    """
    from dataclasses import fields

    from app.memory.scoring import Candidate

    await act_as(conn, seed, "owner_a")
    async with conn.cursor() as cur:
        await cur.execute(
            "select * from app.match_memory(%s, %s::vector, 'anything') limit 0", (seed.org_a, UNIT)
        )
        assert cur.description is not None
        columns = {c.name for c in cur.description}
    wanted = {f.name for f in fields(Candidate)} - {"customer_similarity"}
    assert wanted <= columns, f"retrieval does not return {sorted(wanted - columns)}"
    assert "source_score" in columns  # 0031, named so the E input cannot be missed
    assert "evidence_first_party_only" in columns  # 0040: V6 provenance survives reuse
