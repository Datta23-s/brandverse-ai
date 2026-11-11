"""The 15 requests whose campaigns the human raters score on 16 November (decision-log 2.18, 2.62).

**The selection is STRATIFIED AND BRAND-BALANCED BY DESIGN. It is not a plain random draw**, and the
registration says so: the randomness chooses only between requests the constraints leave free.

REGISTERED 11 November 2026, BEFORE any campaign outcome existed for any request in it. That order
matters more than the method: a subset fixed in advance is pre-registration, and the same subset
chosen after the data exists is not, whatever the seed. At the moment of both draws the evaluation
database held three canary runs of R13 alone, one of them invalidated, so no campaign of any request
in any candidate subset existed and no quality figure could have been computed.

THE RULE (owner, 1 November, re-confirmed 11 November): human quality rating uses a subset of
15 REQUESTS, all three arms of each, so **45 campaigns per rater**; both raters score the same 45,
and kappa per dimension is computed on those 45. The sample stayed at 15 when the scope was cut to
24 requests, so it is 15 of 24 rather than 15 of 30 — a larger share of a smaller set, declared
wherever a quality result is reported.

THE THREE CONSTRAINTS, each a stated part of the sampling design and each decided before any outcome
existed:

1. **Five requests at each campaign position** (5 / 5 / 5). *Why:* position 3 is where the Proposed
   arm's behaviour diverges most, and an unstratified draw that left four requests there would
   underpower the quality comparison exactly where the hypothesis bites.
2. **The regulated brand's request at each position is included** (B07: R19, R20, R21). *Why:* the
   regulated-sector claim needs a quality side, and leaving it to chance risks the one regulated
   brand being absent from the human comparison.
3. **Every brand is represented: a brand not yet covered is preferred over one already covered.**
   *Why:* generalisation across brands is the reason there are eight of them, and stratifying by
   position alone dropped two brands out of the sample entirely.

THE PROCEDURE, written so the 15 are reproducible from the seed alone (no pseudo-random generator,
no library version; `sha256` and a hex-string comparison are the whole of it):

    H(r) = sha256("<seed>|rating:<request id>").hexdigest()
    covered = {}                                  # brands picked so far, across positions
    for position p in 1, 2, 3 (ascending):
        A = the requests at position p, sorted ascending by H
        take = [the request in A whose brand is the regulated brand]    # step 2, before any hash
        covered += its brand
        for r in A, in order: if |take| < 5 and r not in take and brand(r) not in covered:
            take += r; covered += brand(r)                              # step 3, uncovered first
        for r in A, in order: if |take| < 5 and r not in take:
            take += r; covered += brand(r)                              # then the rest
        subset += take
    report the subset sorted by request id

`evaluation/rating_subset.yaml` holds the result, every candidate draw that was considered, and the
reason this one was chosen; `backend/tests/test_rating_subset.py` recomputes the frozen draw from
this code and pins its shape, so the file cannot drift and a quiet re-draw fails a test.

RE-DRAWING A FROZEN SELECTION IS PERMITTED ONLY (a) BEFORE ANY OUTCOME EXISTS FOR ANY REQUEST IN IT,
AND (b) FOR A DESIGN DEFECT STATED IN THE REGISTRATION. **This is not licence.** It is the narrowest
rule that lets a sampling mistake be corrected while no result could possibly be influencing the
correction. Once a rater has scored anything, or an outcome exists for a request in the subset, the
selection is fixed and a change is a new amendment carrying the number of the last scored campaign —
never a re-draw.
"""

import hashlib
from pathlib import Path
from typing import Any

import yaml

from app.evaluation.schedule import ARMS, TEST_SET, test_set_requests

REPO_ROOT = Path(__file__).resolve().parents[3]
RATING_SUBSET = REPO_ROOT / "evaluation" / "rating_subset.yaml"

# Draw 2's seed: the second draw of 2026-11-11, fixed before the draw was computed. Draw 1 (kept in
# the registration as superseded) used 20261111.
SEED = 2026111102
SIZE = 15
PER_POSITION = 5
REGISTERED = "2026-11-11"


def _h(seed: int, request_id: str) -> str:
    return hashlib.sha256(f"{seed}|rating:{request_id}".encode()).hexdigest()


def _request_rows() -> list[dict[str, Any]]:
    """Each runnable request with its brand, position and whether its brand is regulated."""
    data = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))
    regulated = {b["id"] for b in data["brands"] if b["regulated"]}
    runnable = {r["id"] for r in test_set_requests()}
    return [
        {
            "id": r["id"],
            "brand": r["brand"],
            "position": int(r["position"]),
            "regulated": r["brand"] in regulated,
        }
        for r in data["requests"]
        if r["id"] in runnable
    ]


def draw(
    rows: list[dict[str, Any]] | None = None,
    *,
    seed: int = SEED,
    per_position: int = PER_POSITION,
    include_regulated: bool = True,
    prefer_uncovered_brand: bool = True,
) -> list[str]:
    """The subset, by the procedure in the module docstring. The two constraint flags exist so the
    registration can record what each candidate draw did and a test can reproduce all three; the
    frozen draw is the default (both on)."""
    rows = _request_rows() if rows is None else rows
    covered: set[str] = set()
    chosen: list[str] = []
    for position in sorted({r["position"] for r in rows}):
        at_position = sorted(
            (r for r in rows if r["position"] == position), key=lambda r: _h(seed, r["id"])
        )
        take: list[dict[str, Any]] = []
        if include_regulated:
            take = [r for r in at_position if r["regulated"]][:per_position]
            covered |= {r["brand"] for r in take}
        passes = (True, False) if prefer_uncovered_brand else (False,)
        for uncovered_only in passes:
            for row in at_position:
                if len(take) >= per_position:
                    break
                if row in take or (uncovered_only and row["brand"] in covered):
                    continue
                take.append(row)
                covered.add(row["brand"])
        chosen += [r["id"] for r in take]
    return sorted(chosen)


def rating_subset() -> list[str]:
    """The registered subset, computed from this code (the file is its committed copy)."""
    return draw()


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
