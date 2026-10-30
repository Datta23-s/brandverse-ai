"""The LangGraph checkpointer on Postgres (migration 0034), and the run it makes resumable.

The tables are made by a migration, in a schema the REST API does not expose, rather than by the
library's `setup()` at run time: this file holds the migration to the library's own DDL, proves the
API roles cannot read a run's saved state, and kills a worker process mid-run to show the run is
continued from its checkpoint by another process (Phases.md Phase 6 exit criterion).
"""

import subprocess
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from psycopg import AsyncConnection, errors, sql
from psycopg_pool import AsyncConnectionPool

import app.orchestrator.checkpoint as checkpoint
from app.evaluation.llm_attempts import LLM_CALLS_BY_ATTEMPT_SQL, CallRow, totals
from app.models.runs import RunCompleted, StepEvent
from app.orchestrator.checkpoint import (
    LIBRARY_VERSION,
    CheckpointSchemaOutdated,
    assert_checkpoint_schema,
    open_checkpointer,
)
from tests.db.conftest import Seed
from tests.db.graph_support import ListSink, build_pipeline

pytestmark = [pytest.mark.anyio, pytest.mark.db]

BACKEND = Path(__file__).resolve().parents[2]
WORKER_SECONDS = 600  # as tests/db/test_memory_staging.py: imports are slow under machine load
TABLES = ["checkpoint_blobs", "checkpoint_migrations", "checkpoint_writes", "checkpoints"]


def new_run(db_url: str, seed: Seed) -> UUID:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (seed.org_a, seed.product_a),
        ).fetchone()
        assert row is not None
        run_id: UUID = row[0]
        return run_id


def run_row(db_url: str, run_id: UUID) -> tuple[str, bool]:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "select status, completed_at is not null from runs where id = %s", (run_id,)
        ).fetchone()
        assert row is not None
        return row[0], row[1]


def saved_checkpoints(db_url: str, run_id: UUID) -> int:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "select count(*) from graph_checkpoints.checkpoints where thread_id = %s",
            (str(run_id),),
        ).fetchone()
        assert row is not None
        return int(row[0])


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[AsyncConnectionPool[AsyncConnection]]:
    p: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=4, open=False
    )
    await p.open()
    try:
        yield p
    finally:
        await p.close()


# ── The migration is the library's schema, in a private place ────────────────────────────────


def test_the_migrations_tables_are_the_librarys_own_ddl(db_url: str) -> None:
    """Column for column, key for key, index for index: if the library's DDL changes, this fails
    and a new migration is written, rather than the library writing to tables that differ."""
    q_cols = (
        "select table_name, column_name, data_type, is_nullable, column_default "
        "from information_schema.columns where table_schema = %s order by 1, 2"
    )
    q_keys = (
        "select tc.table_name, tc.constraint_type, "
        "array_agg(k.column_name order by k.ordinal_position) "
        "from information_schema.table_constraints tc "
        "join information_schema.key_column_usage k using (constraint_schema, constraint_name) "
        "where tc.table_schema = %s group by tc.table_name, tc.constraint_name, tc.constraint_type "
        "order by 1, 2, 3"
    )
    q_idx = "select tablename, indexname from pg_indexes where schemaname = %s order by 1, 2"
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("drop schema if exists lg_scratch cascade")
        c.execute("create schema lg_scratch")
        try:
            c.execute("set search_path to lg_scratch")
            for ddl in BasePostgresSaver.MIGRATIONS:
                c.execute(ddl)  # type: ignore[call-overload]
            c.execute("reset search_path")
            for query in (q_cols, q_keys):
                assert (
                    c.execute(query, ("lg_scratch",)).fetchall()
                    == c.execute(  # type: ignore[call-overload]
                        query, ("graph_checkpoints",)
                    ).fetchall()
                )
            ours = {i for _, i in c.execute(q_idx, ("graph_checkpoints",)).fetchall()}
            theirs = {i for _, i in c.execute(q_idx, ("lg_scratch",)).fetchall()}
            assert ours == theirs, "the library's own indexes are missing from the migration"
        finally:
            c.execute("drop schema lg_scratch cascade")


async def test_the_schema_check_passes_and_a_newer_library_would_fail_it(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_checkpointer(db_url):
        pass  # assert_checkpoint_schema ran inside
    conn = await AsyncConnection.connect(db_url, autocommit=True)
    try:
        monkeypatch.setattr(checkpoint, "LIBRARY_VERSION", LIBRARY_VERSION + 1)
        from psycopg.rows import dict_row

        conn.row_factory = dict_row  # type: ignore[assignment]
        with pytest.raises(CheckpointSchemaOutdated, match="needs a new migration"):
            await assert_checkpoint_schema(conn)  # type: ignore[arg-type]
    finally:
        await conn.close()


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", TABLES)
def test_the_api_roles_cannot_read_a_runs_saved_state(db_url: str, role: str, table: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as c, c.transaction():
        c.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute(sql.SQL("select * from graph_checkpoints.{}").format(sql.Identifier(table)))


def test_row_level_security_is_on_for_every_checkpoint_table(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as c:
        rows = c.execute(
            "select relname, relrowsecurity from pg_class "
            "where relnamespace = 'graph_checkpoints'::regnamespace and relkind = 'r' order by 1"
        ).fetchall()
    assert rows == [(t, True) for t in TABLES]


# ── A run, end to end, on the real ledger and checkpointer ───────────────────────────────────


async def test_a_run_parks_resumes_and_ends_with_the_executors_statuses(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    assert run_row(db_url, run_id) == ("queued", False)
    async with open_checkpointer(db_url) as saver:
        sink = ListSink()
        pipeline = build_pipeline(pool, saver, sink)
        parked = await pipeline.start(
            run_id=run_id,
            org_id=seed.org_a,
            user_id=seed.users["editor_a"],
            product_id=seed.product_a,
        )
        assert parked.status == "suspended"
        assert run_row(db_url, run_id) == ("awaiting_approval", False)
        assert saved_checkpoints(db_url, run_id) > 0

        # A different pipeline object, as another worker would build it: state comes from the DB.
        other = build_pipeline(pool, saver, ListSink())
        done = await other.resume(run_id, "approve", by=seed.users["approver_a"])
    assert done.status == "completed" and done.state["campaign"]["status"] == "approved"
    assert run_row(db_url, run_id) == ("completed", True)
    steps = [e for e in sink.events if isinstance(e, StepEvent)]
    assert {e.step for e in steps} >= set(range(1, 12))
    assert not any(isinstance(e, RunCompleted) for e in sink.events)  # that was the first worker's


# ── The exit criterion: a killed worker mid-run resumes from its checkpoint ──────────────────


def worker(
    mode: str, db_url: str, run_id: UUID, seed: Seed, log: Path, ready: Path
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, this interpreter
        [
            sys.executable,
            "-m",
            "tests.db.graph_worker",
            mode,
            db_url,
            str(run_id),
            str(seed.org_a),
            str(seed.product_a),
            str(seed.users["approver_a"]),
            str(log),
            str(ready),
        ],
        cwd=BACKEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def finished(proc: subprocess.Popen[bytes]) -> str:
    out, _ = proc.communicate(timeout=WORKER_SECONDS)
    text = out.decode()
    assert proc.returncode == 0, text
    return text


def test_a_killed_worker_mid_run_is_continued_from_its_checkpoint_by_another_process(
    db_url: str, seed: Seed, tmp_path: Path
) -> None:
    """SIGKILL / TerminateProcess in the middle of the parallel agent step: no cleanup code runs.
    A second process continues the run. Everything that finished before the kill is not run again,
    including the two agents of the same step that had already returned; the one that was running
    is run again (at-least-once). A third process approves it."""
    run_id = new_run(db_url, seed)
    log, ready = tmp_path / "executions.log", tmp_path / "ready"

    proc = worker("run", db_url, run_id, seed, log, ready)
    try:
        deadline = time.monotonic() + WORKER_SECONDS
        while not ready.exists():
            assert proc.poll() is None, proc.stdout.read().decode() if proc.stdout else ""
            assert time.monotonic() < deadline, "the worker never reached the agent step"
            time.sleep(0.05)
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    before = Counter(log.read_text(encoding="utf-8").split())
    assert before == {"retrieve": 1, "research": 1, "A1": 1, "A2": 1, "A3": 1}
    assert run_row(db_url, run_id) == ("queued", False)  # a crash writes nothing: Phase 10 sees it
    assert saved_checkpoints(db_url, run_id) > 0

    assert "outcome suspended" in finished(worker("recover", db_url, run_id, seed, log, ready))
    after = Counter(log.read_text(encoding="utf-8").split())
    assert after["retrieve"] == 1 and after["research"] == 1, "finished steps were run again"
    assert after["retrieve_with_personas"] == 1  # step 8, which the dead worker never reached
    assert after["A1"] == 1 and after["A2"] == 1, "finished agents of the same step were run again"
    assert after["A3"] == 2, "the agent that was running when the worker died runs again"
    assert after["A6"] == 1 and after["A7"] == 1  # and the run went on past the kill
    assert run_row(db_url, run_id) == ("awaiting_approval", False)

    assert "outcome completed" in finished(worker("approve", db_url, run_id, seed, log, ready))
    assert run_row(db_url, run_id) == ("completed", True)

    # What the re-execution did to the call log, MEASURED. A3 was killed after its ledger
    # reservation and before it finished: its pending row is still there, and the retry wrote
    # another.
    with psycopg.connect(db_url, autocommit=True) as c:
        calls = c.execute(
            "select agent_id, step, revision_round, outcome, execution_id from llm_calls "
            "where run_id = %s order by id",
            (run_id,),
        ).fetchall()
        recorded = c.execute(
            "select llm_call_count, total_cost_usd from runs where id = %s", (run_id,)
        ).fetchone()
    a3 = [r for r in calls if r[0] == "A3"]
    assert [r[3] for r in a3] == ["pending", "ok"] and a3[0][4] != a3[1][4]
    assert {(r[1], r[2]) for r in calls if r[0] in ("A1", "A2", "A3")} == {(7, 0)}
    assert len([r for r in calls if r[0] in ("A1", "A2")]) == 2  # finished agents were not re-run
    # The run record is SPEND: it counts the abandoned reservation. A1, A2, A3 (killed), A3 (retry).
    assert recorded is not None and recorded[0] == 4
    with psycopg.connect(db_url, autocommit=True) as c, c.cursor() as cur:
        cur.execute(LLM_CALLS_BY_ATTEMPT_SQL, {"org_ids": [seed.org_a]})
        mine = [CallRow(*r) for r in cur.fetchall() if r[1] == run_id]
    t = totals(mine)
    assert (t.spend_calls, t.work_calls, t.resume_duplicate_calls) == (4, 3, 1)
    assert [(r.attempt, r.last_attempt) for r in mine if r.agent_id == "A3"] == [
        (1, False),
        (2, True),
    ]
