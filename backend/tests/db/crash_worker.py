"""Test helper (not a test): run an attempt that stages a memory write, then hang until killed.

Used by test_memory_staging.py to kill a real process mid-run. Writes `ready_file` once the
memory write is staged, then waits forever inside a step.

Usage: python -m tests.db.crash_worker <db_url> <run_id> <org_id> <brand_id> <marker> <ready_file>
"""

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.core.budgeted_run import Step, run_steps
from app.memory.embeddings import HashingEncoder
from app.memory.writeback import MemoryStage, MemoryWrite


class Out(BaseModel):
    step: str


async def main(
    db_url: str, run_id: str, org_id: str, brand_id: str, marker: str, ready: str
) -> None:
    stage = MemoryStage()

    async def stage_memory(_: Mapping[str, BaseModel]) -> BaseModel:
        encoder = HashingEncoder()
        content = f"staged before the crash {marker}"
        stage.stage(
            MemoryWrite(
                org_id=UUID(org_id),
                brand_id=UUID(brand_id),
                kind="insight",
                content=content,
                provenance=marker,
                embedding=tuple(encoder.embed_documents([content])[0]),
                embedding_model=encoder.model,
                embedding_version=encoder.version,
            )
        )
        return Out(step="stage_memory")

    async def hang(_: Mapping[str, BaseModel]) -> BaseModel:
        await asyncio.to_thread(Path(ready).write_text, "staged", encoding="utf-8")
        await asyncio.sleep(3600)
        return Out(step="hang")

    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    try:
        await run_steps(
            [Step("stage_memory", stage_memory), Step("hang", hang)],
            pool=pool,
            run_id=UUID(run_id),
            memory=stage,
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:7]), loop_factory=asyncio.SelectorEventLoop)
