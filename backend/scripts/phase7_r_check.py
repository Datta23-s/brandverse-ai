"""Stage 1 of the Phase 7 freeze gate: does `R` separate campaigns 1, 2 and 3 of a brand?

The prediction was registered BEFORE this was run (`docs/phase7-first-decision.md`, "Registered
prediction"): campaign 1 -> D2, campaign 2 -> D1, campaign 3 -> D0. If the three campaigns land
in the same band the adaptive mechanism is inert and the work stops (owner, 1 November 2026).

This stage uses REAL memory and no model call. The throwaway database of chunk F's first real
campaign (`brandverse_chunk_f`) holds the items that campaign wrote for brand B04 (request R10).
For that brand's campaign 2 (R11, the other product) and campaign 3 (R12, the first product again)
it runs the real step-4 recall (the local encoder; the same `recall` the pipeline calls) and the
formula as it would be implemented:

    R = 0.30 N + 0.25 U + 0.15 S, renormalised over 0.70  (F and E dropped: decision-log 2.18)
    N = 1 - max cosine to the organisation's earlier requests
    U = 1 - memory_coverage;  S = 1 for a regulated sector
    D0 below 0.35, D1 from 0.35 to below 0.70, D2 from 0.70

Other brands have no memory yet: each needs its campaign 1 run on real models (about 7 Standard
calls a brand), which is a separate step that needs the owner's budget ruling.

Usage (from backend/):  PYTHONPATH=. uv run python -m scripts.phase7_r_check
"""

import asyncio
import statistics
import sys
from datetime import UTC, datetime
from uuid import uuid4

import psycopg

from app.evaluation.test_set import request_inputs
from app.memory.embeddings import default_encoder
from app.memory.recall import recall
from app.memory.scoring import load_scoring

URL = "postgresql://postgres:postgres@127.0.0.1:55432/brandverse_chunk_f"
WEIGHTS = {"N": 0.30, "U": 0.25, "S": 0.15}
D0_BELOW, D2_FROM = 0.35, 0.70


def depth(r: float) -> str:
    return "D0" if r < D0_BELOW else "D1" if r < D2_FROM else "D2"


def r_score(n: float, u: float, s: float) -> float:
    total = sum(WEIGHTS.values())
    return (WEIGHTS["N"] * n + WEIGHTS["U"] * u + WEIGHTS["S"] * s) / total


async def main() -> int:
    inputs = request_inputs()
    enc = default_encoder()
    cfg = load_scoring()

    def text(i: str) -> str:
        r = inputs[i]
        return f"{r.category}. {r.sector}. {r.brief} {r.audience}"

    vec = dict(
        zip(
            ("R10", "R11", "R12"),
            enc.embed_documents([text(i) for i in ("R10", "R11", "R12")]),
            strict=True,
        )
    )

    def cos(a: str, b: str) -> float:
        return float(sum(x * y for x, y in zip(vec[a], vec[b], strict=True)))

    with psycopg.connect(URL, autocommit=True) as c:
        org, brand, product_a = c.execute(
            "select p.org_id, p.brand_id, p.id from products p "
            "join memory_items m on m.brand_id = p.brand_id limit 1"
        ).fetchone() or (None, None, None)
        kinds = c.execute(
            "select kind, count(*) from memory_items where brand_id = %s group by kind", (brand,)
        ).fetchall()
    if org is None:
        print("no memory items found in brandverse_chunk_f: run scripts.chunk_f_first_campaign")
        return 1
    print("B04 memory after campaign 1:", dict(kinds))
    product_b = uuid4()
    rows = []
    for rid, prior, product_id in (
        ("R10", [], None),
        ("R11", ["R10"], product_b),
        ("R12", ["R10", "R11"], product_a),
    ):
        r_in = inputs[rid]
        n = 1.0 if not prior else 1 - max(cos(rid, p) for p in prior)
        if rid == "R10":
            u, slots = 1.0, "0 of 2 (cold start: nothing existed)"
        else:
            async with await psycopg.AsyncConnection.connect(URL) as conn:
                found = await recall(
                    conn,
                    enc,
                    cfg,
                    org_id=org,
                    query_text=f"{r_in.category} {r_in.sector.replace('_', ' ')} {r_in.brief}",
                    product_id=str(product_id),
                    brand_id=str(brand),
                    product_category=r_in.category,
                    sector=r_in.sector,
                    now=datetime.now(UTC),
                    personas=None,
                )
            u = 1.0 - found.coverage
            best = max((i.m for i in found.items), default=0.0)
            slots = f"{found.slots_filled} of {found.slots_total}; best M {best:.3f}"
        s = 0.0  # B04 footwear is not a regulated sector
        r = r_score(n, u, s)
        rows.append((rid, n, u, r, depth(r), slots))
    print("\ncampaign | N     | U     | R     | depth | slots filled (step-4 recall, C left out)")
    for rid, n, u, r, d, slots in rows:
        print(f"{rid:8} | {n:.3f} | {u:.3f} | {r:.3f} | {d:5} | {slots}")
    bands = [row[4] for row in rows]
    print("\nregistered prediction: R10 D2, R11 D1, R12 D0")
    print("produced:             ", ", ".join(f"{r[0]} {r[4]}" for r in rows))
    print("separates (three different bands):", len(set(bands)) == 3)
    print("spread of R:", f"{statistics.pstdev([row[3] for row in rows]):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
