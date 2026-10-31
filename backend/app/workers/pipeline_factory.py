"""Assemble the pipeline the worker runs (Phases.md Phase 6 chunk E).

One `Pipeline` per worker process, built at start-up over the worker's own pool, the run event log
and a checkpointer connection. Two compositions, chosen by `Settings.pipeline_agents`:

- **mock**: the deterministic stand-ins of `app/agents/mock.py`, a lexical statement extractor, no
  memory, a fixed research result and the hashing encoder. No model call and no network. For the
  end-to-end tests and the demo; refused in production.
- **real**: the model-backed agents and the claim classifier through the gateway, under the run's
  call and cost caps (`Rules.md` C3); write-back embeds with the local encoder. LIMITS, stated:
  memory recall is OFF here (`no_memory`; Phase 7 is where memory is consumed), and research at D1
  and D2 REFUSES to search, because live runs wait for the recording to finish (owner, 2026-10-29).
  A real run works at depth D0 today; the refusal is a visible failure, not a silent skip.
"""

from contextlib import AsyncExitStack
from datetime import date

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

import app.orchestrator  # noqa: F401  (sets the strict-msgpack switch before LangGraph is used)
from app.agents.base import Agent
from app.agents.copywriter import CopyWriter
from app.agents.market import MarketAnalyst
from app.agents.mock import lexical_extractor, mock_agents
from app.agents.personas import PersonaAnalyst
from app.agents.product_brand import ProductBrandAnalyst
from app.agents.risk import RiskReader
from app.core.campaigns import PoolCampaignStore
from app.core.config import Settings
from app.core.gates import load_superlatives
from app.errors import BrandverseError
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallLedger
from app.llm.prompts import library_from
from app.llm.registry import registry_from
from app.memory.embeddings import HashingEncoder, default_encoder
from app.orchestrator.adapters import (
    brief_checker,
    classifier_extractor,
    gate_block_recorder,
    pool_context_loader,
    writeback_step,
)
from app.orchestrator.checkpoint import open_checkpointer
from app.orchestrator.deps import EventSink, GraphDeps, ResearchResult, no_memory
from app.orchestrator.graph import build_graph, build_regate
from app.orchestrator.runner import Pipeline, PoolRunLedger
from app.orchestrator.state import Depth, RunState


class LiveResearchNotEnabled(BrandverseError):
    """A real run asked to search while live runs are held for the recording."""


async def _mock_research(state: RunState, depth: Depth) -> ResearchResult:
    return ResearchResult(
        [{"url": "https://example.com/a", "title": "t", "score": 0.7}], {"searches": 0}
    )


async def _refuse_live_research(state: RunState, depth: Depth) -> ResearchResult:
    raise LiveResearchNotEnabled(
        f"depth {depth} needs a live search, which is held until the recording is finished; "
        "run at depth D0"
    )


async def open_pipeline(
    settings: Settings,
    pool: AsyncConnectionPool[AsyncConnection],
    events: EventSink,
    stack: AsyncExitStack,
) -> Pipeline:
    """The worker's pipeline. The checkpointer connection is entered on `stack`, which the worker
    closes at shutdown."""
    if settings.database_url is None:
        raise BrandverseError("DATABASE_URL is not set")
    saver = await stack.enter_async_context(
        open_checkpointer(settings.database_url.get_secret_value())
    )
    today = date.today
    if settings.pipeline_agents == "mock":
        agents: dict[str, Agent] = dict(mock_agents())
        deps = GraphDeps(
            events=events,
            load_context=pool_context_loader(pool),
            retrieve=no_memory,
            research=_mock_research,
            agents=agents,
            write_memory=writeback_step(pool, HashingEncoder()),
            extract_statements=lexical_extractor,
            lexicon=load_superlatives("v1"),
            today=today,
            record_block=gate_block_recorder(pool),
            max_revisions=settings.max_revision_rounds,
        )
    else:
        if settings.google_api_key is None:
            raise BrandverseError("pipeline_agents is 'real' but GOOGLE_API_KEY is not set")
        registry = registry_from(settings.models_config_path)
        prompts = library_from(settings.prompts_dir)
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=settings.max_llm_calls_per_run,
            max_cost_per_run_usd=settings.max_cost_per_run_usd,
            pending_expiry_s=settings.llm_pending_expiry_s,
        )
        gateway = LLMGateway(
            registry,
            ledger,
            prompts,
            api_key=settings.google_api_key.get_secret_value(),
            timeout_s=settings.llm_call_timeout_s,
            max_rate_limit_waits=settings.llm_max_rate_limit_waits,
            max_rate_limit_wait_s=settings.llm_max_rate_limit_wait_s,
            allow_strong_tier=settings.llm_allow_strong_tier,
            strong_tier_org_id=settings.llm_strong_tier_org_id,
        )
        agents = {
            **mock_agents(),
            "A1": ProductBrandAnalyst(gateway, prompts),
            "A2": MarketAnalyst(gateway, prompts),
            "A3": PersonaAnalyst(gateway, prompts),
            "A6": CopyWriter(gateway, prompts),
            "A7": RiskReader(gateway, prompts),
        }
        deps = GraphDeps(
            events=events,
            load_context=pool_context_loader(pool),
            retrieve=no_memory,
            research=_refuse_live_research,
            agents=agents,
            # The local encoder (loaded here, 37 s): items written to memory carry its real vectors.
            write_memory=writeback_step(pool, default_encoder()),
            extract_statements=classifier_extractor(gateway, prompts),
            lexicon=load_superlatives("v1"),
            today=today,
            check_brief=brief_checker(gateway, prompts),
            record_block=gate_block_recorder(pool),
            max_revisions=settings.max_revision_rounds,
        )
    return Pipeline(
        build_graph(deps, saver),
        events,
        PoolRunLedger(pool),
        max_revisions=deps.max_revisions,
        campaigns=PoolCampaignStore(pool),
        regate=build_regate(deps),
    )
