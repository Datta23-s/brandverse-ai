"""Calling `app.match_memory` (migration 0022) with its search depth set by the caller.

`hnsw.ef_search` is a search-time quality-versus-speed parameter, so it is not fixed in the
function: it is set here, transaction-local, from config/scoring.yaml unless the caller passes
another value (an evaluation sweep), and the value in effect is returned and logged with every
retrieval. A session-level setting by the signed-in role needs no privilege; setting it on the
function did (it failed on the live project, 2026-10-18).
"""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.memory.scoring import load_scoring

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Retrieval:
    rows: list[dict[str, Any]]
    ef_search: int  # as reported by the database after setting it, not as requested


async def match_memory(
    conn: AsyncConnection,
    org_id: UUID,
    query_embed: str,
    query_text: str,
    *,
    brand_id: UUID | None = None,
    include_seed: bool = False,
    limit: int = 20,
    ef_search: int | None = None,
) -> Retrieval:
    """Candidates for one query, in the caller's transaction (e.g. `app.core.db.as_user`)."""
    if conn.autocommit:
        # A transaction-local setting would end before the query that needs it.
        raise ValueError("match_memory needs a transaction: the connection is in autocommit")
    depth = ef_search if ef_search is not None else load_scoring().retrieval.hnsw_ef_search
    if not 1 <= depth <= 1000:
        raise ValueError(f"hnsw.ef_search must be 1..1000, got {depth}")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select set_config('hnsw.ef_search', %s, true) as ef_search", (str(depth),)
        )
        applied = await cur.fetchone()
        if applied is None:
            raise RuntimeError("set_config returned no row")
        await cur.execute(
            "select * from app.match_memory(%s, %s::public.vector, %s, p_brand_id => %s, "
            "p_include_seed => %s, p_limit => %s)",
            (org_id, query_embed, query_text, brand_id, include_seed, limit),
        )
        rows = await cur.fetchall()
    used = int(applied["ef_search"])
    log.info(
        "memory retrieval",
        extra={
            "org_id": str(org_id),
            "brand_id": None if brand_id is None else str(brand_id),
            "ef_search": used,
            "include_seed": include_seed,
            "limit": limit,
            "candidates": len(rows),
        },
    )
    return Retrieval(rows, used)
