"""Recall: retrieve candidates, supply the M-score's `C` and `E`, and score them (Phase 6 chunk C).

`app.match_memory` returns the candidate set; `score()` (scoring.py) turns each into an M from the
components that CAN be computed and says which were left out. Until this module, `score()` had no
caller, so no real M had ever been computed and `C` and `E` could not have entered one. This is
the caller, and it supplies both:

- **E** is the item's stored source score (0028, returned by `match_memory` since 0031) times its
  confidence, which `score()` multiplies; a missing source score leaves E out.
- **C** is the customer match, taken here because it depends on the REQUEST's persona and cannot
  be precomputed. Each item carries the persona summaries of the campaigns that produced,
  reinforced or reused it, encoded (`memory_item_personas`, 0037). C is the maximum cosine over
  every (request persona, item persona) pair, clamped to [0, 1] like S (owner, 2026-10-29: the
  maximum, for the same monotonicity reason as E; the set is stored, not an aggregate). Both sides
  are encoded as DOCUMENTS (no query instruction): a persona is compared with a persona, not
  searched for. Only vectors made by the same encoder model and version are compared; an item
  with no persona of that kind has C left out, never zero.

**Two stages in a run, and why.** The request's persona does not exist at step 4: A3 runs at step
7, and what A3 sees comes from step 6, which exists only because step 5 read step 4's coverage.
So step 4 recalls with `personas=None` and `C` is left out of every M it computes (five components
at most), and step 8 (`personalise`), once A3 has spoken, recalls again with the personas, so the
M that REUSE and write-back decisions use has all six components wherever the data exists. Every
item carries the components it was made of and the ones it left out, so a figure never claims a
component it lacks.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.memory.embeddings import Encoder
from app.memory.retrieval import match_memory
from app.memory.scoring import (
    Candidate,
    RequestContext,
    ScoringConfig,
    memory_coverage,
    score,
)
from app.memory.writeback import vector_literal

_CUSTOMER_SQL = """
select p.memory_item_id, max(1 - (p.embedding operator(public.<=>) q.v::public.vector))::float8
from public.memory_item_personas p
cross join unnest(%(q)s::text[]) as q(v)
where p.org_id = %(org)s
  and p.memory_item_id = any(%(ids)s)
  and p.embedding_model = %(model)s
  and p.embedding_version = %(version)s
group by p.memory_item_id
"""


@dataclass(frozen=True, slots=True)
class RecalledItem:
    id: str
    kind: str
    content: str
    m: float
    band: str
    components: dict[str, float]
    left_out: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        """Plain data for run state (a checkpoint is strict msgpack)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "m": round(self.m, 4),
            "band": self.band,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "left_out": list(self.left_out),
        }


@dataclass(frozen=True, slots=True)
class Recall:
    items: list[RecalledItem]
    coverage: float
    ef_search: int
    persona_scored: bool  # the request's personas were supplied, so C was attempted


async def customer_matches(
    conn: AsyncConnection,
    encoder: Encoder,
    org_id: UUID,
    item_ids: Sequence[UUID],
    personas: Sequence[str],
) -> dict[str, float]:
    """item id -> C for the items that have persona context from this encoder; others are absent."""
    if not item_ids or not personas:
        return {}
    vectors = [vector_literal(v) for v in encoder.embed_documents(list(personas))]
    cur = await conn.execute(
        _CUSTOMER_SQL,
        {
            "q": vectors,
            "org": org_id,
            "ids": list(item_ids),
            "model": encoder.model,
            "version": encoder.version,
        },
    )
    return {str(item): min(1.0, max(0.0, float(c))) for item, c in await cur.fetchall()}


async def recall(
    conn: AsyncConnection,
    encoder: Encoder,
    cfg: ScoringConfig,
    *,
    org_id: UUID,
    query_text: str,
    product_id: str | None,
    brand_id: str | None,
    product_category: str | None,
    sector: str | None,
    now: datetime,
    personas: Sequence[str] | None = None,
    limit: int = 20,
) -> Recall:
    """Candidates for one request, scored. `personas` is None before A3 has spoken (C left out)."""
    query = encoder.embed_queries([query_text])[0]
    found = await match_memory(
        conn,
        org_id,
        vector_literal(query),
        query_text,
        brand_id=None if brand_id is None else UUID(brand_id),
        limit=limit,
    )
    ids = [r["id"] for r in found.rows]
    customer = await customer_matches(conn, encoder, org_id, ids, personas or ())
    ctx = RequestContext(product_id, brand_id, product_category, sector, now)

    def num(v: Any) -> float | None:
        return None if v is None else float(v)

    items: list[RecalledItem] = []
    for r in found.rows:
        candidate = Candidate(
            id=str(r["id"]),
            kind=r["kind"],
            brand_id=None if r["brand_id"] is None else str(r["brand_id"]),
            product_id=None if r["product_id"] is None else str(r["product_id"]),
            product_category=r["product_category"],
            claim_type=r["claim_type"],
            sector=r["sector"],
            confidence=num(r["confidence"]),
            created_at=r["created_at"],
            freshness_days=r["freshness_days"],
            similarity=num(r["similarity"]),
            customer_similarity=customer.get(str(r["id"])),
            source_score=num(r["source_score"]),
        )
        s = score(candidate, ctx, cfg.memory)
        items.append(
            RecalledItem(
                candidate.id,
                candidate.kind,
                r["content"],
                s.m,
                s.band,
                {str(k): v for k, v in s.components.items()},
                tuple(s.left_out),
            )
        )
    items.sort(key=lambda i: i.m, reverse=True)
    best_by_slot: dict[str, Sequence[float]] = {
        slot: [i.m for i in items if i.kind == slot] for slot in cfg.retrieval.required_slots
    }
    return Recall(
        items, memory_coverage(best_by_slot, cfg.memory), found.ef_search, personas is not None
    )
