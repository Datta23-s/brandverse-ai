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
    S = 1 for a regulated sector
    U = 1 - min(1, n / 12), n = recalled items whose semantic match S reaches 0.55   (REDEFINED
        1 November 2026, decision-log 2.21; the first definition, slots whose best M crosses
        0.80, is what stage 1 failed on and is not used here)
    D0 below 0.20, D1 from 0.20 to below 0.32, D2 from 0.32   (frozen provisional)

All of those numbers are read from `config/scoring.yaml` `necessity:`, pinned by
tests/test_necessity_frozen.py. Run on B04 this is a RE-SCORE of data whose stage-1 result was
already seen, so it cannot test the registered prediction; it is reported as contaminated.

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
from app.memory.scoring import (
    load_scoring,
    necessity_depth,
    necessity_r,
    quantity_coverage,
)

URL = "postgresql://postgres:postgres@127.0.0.1:55432/brandverse_chunk_f"


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
            u, slots = 1.0, "n = 0 (cold start: nothing existed)"
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
            sims = [i.components["S"] for i in found.items if "S" in i.components]
            count, coverage = quantity_coverage(sims, cfg.necessity)
            u = 1.0 - coverage
            floor = cfg.necessity.relevance_floor
            slots = f"n = {count} of {len(found.items)} recalled pass S >= {floor}"
        s = 1.0 if r_in.regulated else 0.0
        r = necessity_r(n, u, s, cfg.necessity)
        rows.append((rid, n, u, r, necessity_depth(r, cfg.necessity), slots))
    print("\ncampaign | N     | U     | R     | depth | relevant items (step-4 recall, C left out)")
    for rid, n, u, r, d, slots in rows:
        print(f"{rid:8} | {n:.3f} | {u:.3f} | {r:.3f} | {d:5} | {slots}")
    bands = [row[4] for row in rows]
    print("\nstage-1 definition's prediction: R10 D2, R11 D1, R12 D0 (it failed: D2, D1, D1)")
    print("this definition produces:", ", ".join(f"{r[0]} {r[4]}" for r in rows))
    print("separates (three different bands):", len(set(bands)) == 3)
    print("spread of R:", f"{statistics.pstdev([row[3] for row in rows]):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
