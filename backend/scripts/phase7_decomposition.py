"""ZERO model calls: how much of each variant's depth reduction is memory and how much is novelty.

Reads the committed stage-2 reports (every campaign's logged N and U per variant) and prints, per
variant and per attempt, the decomposition and the memory share of the reduction. OLD is the
NEGATIVE CONTROL: its U stays 1.00, so any reduction it shows is novelty against its own bands, and
its column is reported beside COUNT's, never dropped (owner, 2026-11-06; decision-log 2.45). For the
evaluation itself the same functions read the engine's record from `campaigns.strategy`
(`app.evaluation.necessity_decomposition.necessity_records`).

    PYTHONPATH=. uv run python -m scripts.phase7_decomposition
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STAGE2_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "stage2"
REPORTS = (
    ("attempt 23 (A2 reads 6 pages)", "stage2_interim_20261106.json"),
    ("attempt 24 (A2 reads 10 pages)", "stage2_interim_20261106_attempt24.json"),
)
VARIANTS = ("old", "floor", "count")


def main() -> int:
    from app.evaluation.necessity_decomposition import Bands, attribute, decompose
    from app.memory.scoring import load_scoring
    from scripts.phase7_stage2 import OLD_D0_BELOW, OLD_D2_FROM

    cfg = load_scoring().necessity
    bands = {
        "old": Bands(OLD_D0_BELOW, OLD_D2_FROM),
        "floor": Bands(cfg.d0_below, cfg.d2_from),
        "count": Bands(cfg.d0_below, cfg.d2_from),
    }
    for label, name in REPORTS:
        report = json.loads((STAGE2_DIR / name).read_text(encoding="utf-8"))
        print(f"\n=== {label} ===")
        for variant in VARIANTS:
            ds = [
                decompose(
                    cfg.weights,
                    bands[variant],
                    novelty=float(c["novelty"]),
                    u=float(c[f"u_{variant}"]),
                    s_sector=0.0,
                )
                for cs in report["brands"].values()
                for c in cs
                if c["position"] > 1
            ]
            tag = " (NEGATIVE CONTROL: U stays 1.00)" if variant == "old" else ""
            print(f"  {variant.upper():5}{tag}: {attribute(ds).line()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
