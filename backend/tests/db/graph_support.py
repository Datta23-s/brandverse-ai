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
from app.agents.mock import lexical_extractor, mock_agents
from app.core.gates import load_superlatives
from app.llm.ledger import CallLedger
from app.llm.prompts import PromptRef
from app.llm.registry import Tier
from app.memory.embeddings import HashingEncoder
from app.models.runs import RunEvent
from app.orchestrator.adapters import pool_context_loader, writeback_step
from app.orchestrator.deps import GraphDeps, ResearchResult, no_memory
from app.orchestrator.graph import build_graph
from app.orchestrator.runner import Pipeline, PoolRunLedger
from app.orchestrator.state import RunState
from tests.llm_fakes import judge, make_registry

ON = date(2026, 10, 29)


class ListSink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"{len(self.events)}-0"


class Failing:
    """An agent that raises (a budget refusal or any other error) instead of answering."""

    def __init__(self, agent_id: str, error: Exception) -> None:
        self.id, self.error = agent_id, error

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
        raise self.error


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
        ledger: CallLedger | None = None,
        prompt: PromptRef | None = None,
    ) -> None:
        self.inner, self.id, self.log = inner, inner.id, log
        self.hang_after, self.ready = hang_after, ready
        self.ledger, self.prompt = ledger, prompt

    async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> BaseModel:
        note(self.log, self.id)
        call_id: int | None = None
        if self.ledger is not None and self.prompt is not None:
            # A real ledger reservation, as the gateway makes before a model call: pending until
            # `finish`. A worker killed past this point leaves the pending row behind.
            admission = await self.ledger.admit(
                ctx.call_context(self.id), Tier.CHEAP, self.prompt, "test", 0.0001
            )
            call_id = admission.call_id
        if self.hang_after is not None and self.log is not None and self.ready is not None:
            while not all(  # noqa: ASYNC110 - polls a file that another process writes
                a in self.log.read_text(encoding="utf-8").split() for a in self.hang_after
            ):
                await asyncio.sleep(0.05)
            await asyncio.sleep(1.0)  # let the finished agents' results reach the checkpoint
            self.ready.write_text("hanging")
            await asyncio.Event().wait()  # until the test kills this process
        out = await self.inner.run(state, ctx)
        if self.ledger is not None and call_id is not None:
            await self.ledger.finish(
                call_id,
                outcome="ok",
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.001,
                latency_ms=10,
                rate_limit_wait_ms=0,
            )
        return out


def build_pipeline(
    pool: AsyncConnectionPool[AsyncConnection],
    saver: AsyncPostgresSaver,
    sink: ListSink,
    *,
    log: Path | None = None,
    hang: Path | None = None,
    calls: bool = False,
    failing: Mapping[str, Exception] | None = None,
) -> Pipeline:
    ledger = (
        CallLedger(
            pool,
            make_registry(rpm=1000),
            max_calls_per_run=20,
            max_cost_per_run_usd=2.50,
            pending_expiry_s=900,
        )
        if calls
        else None
    )
    agents: dict[str, Agent] = {
        a: Logged(
            impl,
            log,
            hang_after=("A1", "A2") if a == "A3" and hang is not None else None,
            ready=hang if a == "A3" else None,
            ledger=ledger if a in ("A1", "A2", "A3") else None,
            prompt=judge("x").ref,
        )
        for a, impl in mock_agents().items()
    }
    for agent_id, error in (failing or {}).items():
        agents[agent_id] = Failing(agent_id, error)

    async def retrieve(state: RunState, personas: Any = None) -> Any:
        note(log, "retrieve_with_personas" if personas else "retrieve")
        return await no_memory(state, personas)

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
        extract_statements=lexical_extractor,
        lexicon=load_superlatives("v1"),
        today=lambda: ON,
    )
    return Pipeline(
        build_graph(deps, saver), sink, PoolRunLedger(pool), max_revisions=deps.max_revisions
    )
