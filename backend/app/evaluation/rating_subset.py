"""The 15 requests whose campaigns the human raters score on 16 November (decision-log 2.18, 2.59).

REGISTERED 11 November 2026, BEFORE the campaigns of those requests existed and before any quality
figure could be computed. That order matters more than the method: a random subset fixed in advance
is pre-registration, and the same subset chosen after the data exists is not, whatever the seed.

THE RULE (owner, 1 November, re-confirmed 11 November): **human quality rating uses a random subset
of 15 REQUESTS, all three arms of each, so 45 campaigns per rater**; both raters score the same 45,
and kappa per dimension is computed on those 45. The subset stayed at 15 when the
scope was cut to 24 requests, so it is now 15 of 24 rather than 15 of 30 — a larger share of a
smaller set, which is declared wherever a quality result is reported.

THE METHOD is the run order's, for the same reason (`app/evaluation/schedule.py`): each request is
sorted by `sha256("<seed>|rating:<request id>")` and the first 15 are taken. No pseudo-random
generator and no library version, so anyone can recompute the draw by hand from the seed. The seed
is the date of the draw, as 20261105 was the run order's; it was fixed before the draw was computed
and the draw was computed once. `evaluation/rating_subset.yaml` holds the result and
`backend/tests/test_rating_subset.py` recomputes it, so the file cannot drift from this code.

WHAT THE DRAW HAPPENED TO GIVE, recorded as an outcome of the seed and NOT as a property of the
design: all eight brands have at least one request in the subset, and the regulated brand B07 has
all three of its campaigns in it. The campaign positions are NOT balanced — 4 at position 1, 7 at
position 2, 4 at position 3 — so the raters see more second campaigns than first or third. That is a
consequence of an unstratified draw, it is reported with the quality figures, and it is not
corrected by re-drawing: re-drawing until the shape looks better is exactly what registering a seed
in advance exists to prevent.
"""

import hashlib
from pathlib import Path
from typing import Any

import yaml

from app.evaluation.schedule import ARMS, test_set_requests

REPO_ROOT = Path(__file__).resolve().parents[3]
RATING_SUBSET = REPO_ROOT / "evaluation" / "rating_subset.yaml"

# Fixed before the draw was computed; the date of the draw, as the run order's seed was.
SEED = 20261111
# The sample size the owner holds at 15 requests (45 campaigns per rater).
SIZE = 15
REGISTERED = "2026-11-11"


def _key(seed: int, request_id: str) -> str:
    return hashlib.sha256(f"{seed}|rating:{request_id}".encode()).hexdigest()


def draw(request_ids: list[str], size: int = SIZE, seed: int = SEED) -> list[str]:
    """The `size` requests the raters score, in request-id order (the presentation order is the
    blind harness's business, not this draw's)."""
    if size > len(request_ids):
        raise ValueError(f"cannot draw {size} of {len(request_ids)} requests")
    return sorted(sorted(request_ids, key=lambda r: _key(seed, r))[:size])


def rating_subset() -> list[str]:
    """The registered subset, computed from the code (the file is the committed copy of it)."""
    return draw([r["id"] for r in test_set_requests()])


def campaigns_in_subset() -> int:
    return len(rating_subset()) * len(ARMS)


def subset_sha256(request_ids: list[str] | None = None) -> str:
    chosen = rating_subset() if request_ids is None else request_ids
    return hashlib.sha256("\n".join(chosen).encode()).hexdigest()


def registered_file(path: Path = RATING_SUBSET) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a registration")
    return data
