"""The graph wired to Postgres: the real checkpointer, run ledger and context loader, with mocked
agents, a fake research port and no network. Shared by the in-process test and the subprocess
worker that gets killed (tests/db/graph_worker.py), so both run the same assembly.
"""

import asyncio
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

import app.orchestrator  # noqa: F401  (sets the strict-msgpack switch before LangGraph is used)
from app.agents.base import Agent, AgentContext
from app.agents.mock import mock_agents
from app.core.gates import load_superlatives
from app.memory.embeddings import HashingEncoder
from app.models.runs import RunEvent
from app.orchestrator.adapters import pool_context_loader, writeback_step
from app.orchestrator.deps import GraphDeps, ResearchResult, no_memory
from app.orchestrator.graph import build_graph
from app.orchestrator.runner import Pipeline, PoolRunLedger
from app.orchestrator.state import RunState

ON = date(2026, 10, 29)


class ListSink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"{len(self.events)}-0"


def note(path: Path | None, line: str) -> None:
    """Append one line to the execution log: which node bodies ran, in which process."""
    if path is not None:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()


class Logged:
    """Wraps an agent so each execution is recorded; optionally hangs (to be killed) once the
    named other agents have finished."""

    def __init__(
        self,
        inner: Agent,
        log: Path | None,
        *,
        hang_after: tuple[str, ...] | None = None,
        ready: Path | None = None,
    ) -> None:
        self.inner, self.id, self.log = inner, inner.id, log
        self.hang_after, self.ready = hang_after, ready

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
        note(self.log, self.id)
        if self.hang_after is not None and self.log is not None and self.ready is not None:
            while not all(  # noqa: ASYNC110 - polls a file that another process writes
                a in self.log.read_text(encoding="utf-8").split() for a in self.hang_after
            ):
                await asyncio.sleep(0.05)
            await asyncio.sleep(1.0)  # let the finished agents' results reach the checkpoint
            self.ready.write_text("hanging")
            await asyncio.Event().wait()  # until the test kills this process
        return await self.inner.run(state, ctx)


def build_pipeline(
    pool: AsyncConnectionPool[AsyncConnection],
    saver: AsyncPostgresSaver,
    sink: ListSink,
    *,
    log: Path | None = None,
    hang: Path | None = None,
) -> Pipeline:
    agents: dict[str, Agent] = {
        a: Logged(
            impl,
            log,
            hang_after=("A1", "A2") if a == "A3" and hang is not None else None,
            ready=hang if a == "A3" else None,
        )
        for a, impl in mock_agents().items()
    }

    async def retrieve(state: RunState) -> Any:
        note(log, "retrieve")
        return await no_memory(state)

    async def research(state: RunState, depth: Any) -> ResearchResult:
        note(log, "research")
        return ResearchResult([{"url": "https://example.com/a", "title": "t", "score": 0.7}], {})

    deps = GraphDeps(
        events=sink,
        load_context=pool_context_loader(pool),
        retrieve=retrieve,
        research=research,
        agents=agents,
        write_memory=writeback_step(pool, HashingEncoder()),
        lexicon=load_superlatives("v1"),
        today=lambda: ON,
    )
    return Pipeline(
        build_graph(deps, saver), sink, PoolRunLedger(pool), max_revisions=deps.max_revisions
    )
