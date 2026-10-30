"""The brief check: which property assertions in a request's brief no listed claim backs (V11).

FLAG AND RECORD, never block (owner, 2026-10-30): blocking would refuse every request whose brief
mentions an attribute the claims lack, and how many of the evaluation's requests that is has not
been counted. Decide blocking from the number, not before it.

It is a property of the REQUEST, like the planner's query list, not of the run. So for the
evaluation it is PRECOMPUTED once per request and stored with the request's frozen inputs
(`app/evaluation/brief_checks.py`, `evaluation/brief_checks.json`); every arm reads that same
record, so the three arms of a request cannot be flagged differently by a model's variation: the
confound that holding the searches constant rules out, applied to a new input. A run outside the
evaluation computes its own at admission (`nodes.check_brief`). Either way the run keeps the result
in its state and, once it ends, in `runs.brief_check` (migration 0038).

A record is only valid for the brief and the claims it was made from: it carries their hashes, and a
run whose brief or claims no longer match them records `status: stale` and uses nothing, rather than
silently trusting a record about something else.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from app.core.claim_classifier import truth_context
from app.core.gates import Statement
from app.core.sector_registry import verbatim_form
from app.core.truth import ProductTruth

PROMPT = "claim_extract@v2"


def brief_sha256(brief: str) -> str:
    return hashlib.sha256(" ".join(brief.split()).encode("utf-8")).hexdigest()


def claims_sha256(texts: Iterable[str]) -> str:
    """Over the TEXTS of the claims the model is shown (ids differ between databases)."""
    blob = json.dumps(sorted(" ".join(t.split()) for t in texts), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def listed_claim_texts(truth: ProductTruth) -> list[str]:
    return [c.text for c in truth_context(truth)]


def assess(brief: str, truth: ProductTruth, statements: Sequence[Statement]) -> dict[str, Any]:
    """The check from the classifier's statements. A statement counts only if it is a quote of the
    brief; one is unbacked unless it cites a claim the model was shown."""
    listed = {c.id for c in truth_context(truth)}
    form = verbatim_form(brief)
    said = [s for s in statements if s.text.strip() and verbatim_form(s.text) in form]
    unbacked = [s for s in said if s.claim_id not in listed]
    return {
        "status": "checked",
        "unbacked": list(dict.fromkeys(" ".join(s.text.split()) for s in unbacked)),
        "backed": len(said) - len(unbacked),
        "prompt": PROMPT,
        "brief_sha256": brief_sha256(brief),
        "claims_sha256": claims_sha256(listed_claim_texts(truth)),
    }


def reconcile(record: dict[str, Any], brief: str, truth: ProductTruth) -> dict[str, Any]:
    """A precomputed record, as this run uses it: valid only for the brief and claims it was made
    from. Otherwise `stale`, with nothing carried over."""
    fresh = (
        record.get("status") == "checked"
        and record.get("brief_sha256") == brief_sha256(brief)
        and record.get("claims_sha256") == claims_sha256(listed_claim_texts(truth))
    )
    if fresh:
        return {**record, "source": "precomputed"}
    return {
        "status": "stale",
        "unbacked": [],
        "backed": 0,
        "source": "precomputed",
        "record_status": record.get("status"),
    }
