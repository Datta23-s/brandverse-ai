"""The registered evaluation run order (owner ruling, 5 November 2026; docs/evaluation-plan.md,
"Run order: interleaved arms").

THE RULE. The evaluation does not run arm by arm. For each request the three arms run ADJACENTLY,
so a provider outage lands across arms and not inside one. The order is fixed by a seed registered
before any evaluation call, and the resulting order is committed as `evaluation/run_order.yaml`,
which a test recomputes from the seed so the file cannot drift from this code.

WHAT IS RANDOMISED, AND WHAT IS NOT. The test set's campaign pattern is sequential by design
(evaluation/test_set.yaml): campaign 1 of a brand is a cold start, campaign 2 and 3 read the memory
that the brand's earlier approved campaigns wrote. A shuffle of all 30 requests would change what
the Proposed arm remembers, so the randomisation is at the level that does not:

* the 10 BRAND BLOCKS are put in a seeded order;
* inside a brand the campaigns keep their registered position (1, 2, 3);
* inside a campaign the three arms run adjacently, in a seeded order that differs by request, so
  no arm is systematically first.

DO NOT "FIX" THE IN-BRAND ORDER. Owner, 5 November 2026: shuffle the brand blocks and the arm order
within each request, never the campaigns within a brand. Campaigns 1, 2 and 3 are the memory
progression (the experiment's independent variable), and a full shuffle would destroy what it
measures. `tests/test_run_order.py` fails if a brand's campaigns leave positions 1, 2, 3.

The "random" order is a hash ordering, not a pseudo-random generator: each item is sorted by
sha256("<seed>|<label>"). That is fully specified, depends on no library version, and anyone can
recompute it by hand.

Outage windows seen while the evaluation runs are recorded in `evaluation/outage_windows.yaml`
(app/evaluation/outages.py), so any run that touched one can be identified afterwards.
"""

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SET = REPO_ROOT / "evaluation" / "test_set.yaml"
RUN_ORDER = REPO_ROOT / "evaluation" / "run_order.yaml"

# The arms, as `runs.arm` spells them (migration 0005). Order here is irrelevant: it is re-ordered.
ARMS: tuple[str, ...] = ("baseline_a", "baseline_b", "proposed")

# Registered 2026-11-05, before any evaluation call. Chosen as the date it was registered: not
# derived from anything, not tried against an outcome, not to be changed after the first call.
SEED = 20261105


def _key(seed: int, label: str) -> str:
    return hashlib.sha256(f"{seed}|{label}".encode()).hexdigest()


def interleaved_order(
    requests: Sequence[Mapping[str, Any]],
    arms: Sequence[str] = ARMS,
    seed: int = SEED,
) -> list[tuple[str, str]]:
    """[(request_id, arm), ...] in the order the evaluation runs them.

    `requests` are the test set's rows (`id`, `brand`, `position`). See the module docstring."""
    if len(set(arms)) != len(arms):
        raise ValueError("arms must be distinct")
    by_brand: dict[str, list[Mapping[str, Any]]] = {}
    for request in requests:
        by_brand.setdefault(str(request["brand"]), []).append(request)
    for brand, rows in by_brand.items():
        positions = sorted(int(r["position"]) for r in rows)
        if positions != list(range(1, len(rows) + 1)):
            raise ValueError(f"brand {brand}: campaign positions must be 1..n, got {positions}")
    order: list[tuple[str, str]] = []
    for brand in sorted(by_brand, key=lambda b: _key(seed, f"brand:{b}")):
        for request in sorted(by_brand[brand], key=lambda r: int(r["position"])):
            rid = str(request["id"])
            for arm in sorted(arms, key=lambda a: _key(seed, f"arm:{rid}:{a}")):
                order.append((rid, arm))
    return order


def test_set_requests(path: Path = TEST_SET) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        {"id": r["id"], "brand": r["brand"], "position": r["position"]} for r in data["requests"]
    ]


def registered_order() -> list[tuple[str, str]]:
    """The order under the registered seed, from the test set."""
    return interleaved_order(test_set_requests())
