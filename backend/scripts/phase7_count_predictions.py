"""ZERO model calls: the COUNT engine's predicted depth per campaign per brand, whole structure.

Registered in docs/phase7-first-decision.md section 7b (6 November 2026), before any evaluation
run; this script reproduces the table there.

Model, stated BEFORE any evaluation run: campaign 1 is D2 (cold start). Campaign 1 writes n1 items,
n1 drawn from the five first-campaign yields observed at the repaired D2 budget (10 pages): 9, 6,
12, 3 (attempt 24) and 7 (the confirmation run). A campaign at D1 writes ~5 (observed mean
4.5-5), at D2 ~7, at D0 nothing (A2 does not run: decision-log 2.38). Campaign k recalls
everything the brand wrote before it. N is computed from the real request texts with the local
encoder. R, U and the depth come from the frozen functions through the engine's own `decide`.

    PYTHONPATH=. uv run python -m scripts.phase7_count_predictions
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

N1_DIST = (3, 6, 7, 9, 12)
WRITES = {"D0": 0, "D1": 5, "D2": 7}
SEARCHES = {"D0": 1, "D1": 4, "D2": 8}  # D0 counts the mandatory fresh query
RANK = {"D0": 0, "D1": 1, "D2": 2}
BASELINE_B_SEARCHES = 24  # 3 campaigns at D2


def main() -> int:
    from app.evaluation.test_set import plan_requests, request_inputs
    from app.memory.embeddings import default_encoder
    from app.memory.necessity import decide, novelty_against, request_text
    from app.memory.scoring import load_scoring
    from scripts.phase7_stage2 import brand_requests

    cfg = load_scoring().necessity
    enc = default_encoder()
    inputs, reqs, plan = request_inputs(), brand_requests(), plan_requests()

    def depth(n: int, nov: float, reg: bool) -> str:
        return decide(
            cfg,
            novelty=nov,
            similarities=[1.0] * n,
            regulated=reg,
            requested="D1",
            policy="necessity",
        ).depth

    rows = {}
    for b, rids in sorted(reqs.items()):
        inp = [inputs[r] for r in rids]
        vecs = [
            enc.embed_documents([request_text(i.category, i.sector, i.brief, i.audience)])[0]
            for i in inp
        ]
        nov = [1.0, novelty_against(vecs[1], [vecs[0]]), novelty_against(vecs[2], vecs[:2])]
        reg = bool(plan[rids[0]].regulated)
        per = {}
        for n1 in N1_DIST:
            d2 = depth(min(n1, 12), nov[1], reg)
            d3 = depth(min(n1 + WRITES[d2], 12), nov[2], reg)
            per[n1] = (d2, d3)
        rows[b] = (inp[0].sector, reg, nov, per)

    print("brand sector regulated | N2 N3 | C2/C3 depth by C1 yield n1 in", N1_DIST)
    for b, (sector, reg, nov, per) in rows.items():
        cells = " ".join(f"{n1}:{d2}/{d3}" for n1, (d2, d3) in per.items())
        print(f"{b} {sector:22} {reg!s:5} | {nov[1]:.3f} {nov[2]:.3f} | {cells}")

    shares, total = [], 0.0
    print("\nPer brand (modal at n1 = 7; alternatives over the distribution):")
    for b, (_sector, _reg, _nov, per) in rows.items():
        m2, m3 = per[7]
        alt2 = sorted({p[0] for p in per.values()} - {m2}, key=RANK.__getitem__, reverse=True)
        alt3 = sorted({p[1] for p in per.values()} - {m3}, key=RANK.__getitem__, reverse=True)
        reduced = sum(1 for p in per.values() if p[0] != "D2" or p[1] != "D2") / len(per)
        searches = 8 + sum(SEARCHES[p[0]] + SEARCHES[p[1]] for p in per.values()) / len(per)
        shares.append(reduced)
        total += searches
        print(
            f"{b}: C1 D2 | C2 {m2} (alt {', '.join(alt2) or '-'}) | C3 {m3} "
            f"(alt {', '.join(alt3) or '-'}) | P(reduction by C3) {reduced:.2f} "
            f"E[searches] {searches:.1f}"
        )
    mean = total / len(rows)
    share = sum(shares) / len(shares)
    srr = (1 - mean / BASELINE_B_SEARCHES) * 100
    print(f"\nexpected share of brands with ANY reduction by campaign 3: {share:.2f}")
    print(f"expected searches {mean:.1f} vs {BASELINE_B_SEARCHES}: SRR {srr:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
