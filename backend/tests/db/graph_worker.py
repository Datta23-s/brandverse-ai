"""A worker process for the kill/resume test (tests/db/test_graph_checkpoints.py).

    python -m tests.db.graph_worker MODE DB_URL RUN_ID ORG_ID PRODUCT_ID USER_ID LOG READY

MODE `run` starts the run and hangs inside agent A3 once A1 and A2 have finished, writing READY, so
the test can kill the process with the run half-way through a parallel step. `recover` continues the
run from its checkpoint in a NEW process; `approve` resumes it past the interrupt. Every node body
that runs appends a line to LOG, which is how the test sees what was re-run.
"""

import asyncio
import sys
from pathlib import Path
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.orchestrator.checkpoint import open_checkpointer
from tests.db.graph_support import ListSink, build_pipeline


async def main(mode: str, db_url: str, run, org, product, user, log: Path, ready: Path) -> None:  # type: ignore[no-untyped-def]
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    try:
        async with open_checkpointer(db_url) as saver:
            sink = ListSink()
            pipeline = build_pipeline(
                pool, saver, sink, log=log, hang=ready if mode == "run" else None, calls=True
            )
            if mode == "run":
                await pipeline.start(run_id=run, org_id=org, user_id=user, product_id=product)
            elif mode == "recover":
                outcome = await pipeline.recover(run)
                sys.stdout.write(f"outcome {outcome.status}" + chr(10))
            elif mode == "approve":
                outcome = await pipeline.resume(run, "approve", by=user)
                sys.stdout.write(f"outcome {outcome.status}" + chr(10))
            else:
                raise SystemExit(f"unknown mode {mode}")
    finally:
        await pool.close()


if __name__ == "__main__":
    m, url, r, o, p, u, lg, rd = sys.argv[1:9]
    asyncio.run(
        main(m, url, UUID(r), UUID(o), UUID(p), UUID(u), Path(lg), Path(rd)),
        loop_factory=asyncio.SelectorEventLoop,
    )
