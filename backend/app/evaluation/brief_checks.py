"""The evaluation's brief checks: one per REQUEST, computed once, frozen with the request's inputs.

`evaluation/brief_checks.json` holds, for each request, which property assertions in its brief no
verified claim backs (app/core/brief_check.py). It is a property of the request, like the planner's
query list, so it is PRECOMPUTED once and every arm reads the same record: three arms of one request
cannot be flagged differently by a model's variation, which is the confound that holding the
searches constant rules out, applied to a new input (docs/evaluation-plan.md, "The brief check").
30 calls, not 90, and identical by construction.

`evaluation/brief_checks.lock` holds the file's sha256, like `test_set.categories.lock`: changing a
record is a deliberate act that changes the lock, and a run refuses a file that does not match it.
A record also carries the hashes of the brief and the claims it was made from, so a change to the
test set that the record was not remade for shows as stale (`stale_requests`), never as a pass.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.brief_check import brief_sha256, claims_sha256
from app.evaluation.test_set import REPO_ROOT, RequestInputs, request_inputs

FILE = REPO_ROOT / "evaluation" / "brief_checks.json"
LOCK = REPO_ROOT / "evaluation" / "brief_checks.lock"


class FrozenBriefChecksError(Exception):
    """The frozen file is missing, does not match its lock, or does not cover the request."""


def file_sha256(path: Path = FILE) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def freeze(records: dict[str, dict[str, Any]], path: Path = FILE, lock: Path = LOCK) -> None:
    body = json.dumps({"version": 1, "requests": records}, indent=1, sort_keys=True)
    path.write_bytes((body + "\n").encode("utf-8"))
    lock.write_bytes((file_sha256(path) + "\n").encode("utf-8"))


def load(path: Path = FILE, lock: Path = LOCK) -> dict[str, dict[str, Any]]:
    """The frozen records by request id, refusing a file that does not match its lock."""
    if not path.is_file() or not lock.is_file():
        raise FrozenBriefChecksError(
            f"{path.name} or its lock is missing: run the precompute first"
        )
    if file_sha256(path) != lock.read_text(encoding="utf-8").strip():
        raise FrozenBriefChecksError(f"{path.name} does not match {lock.name}")
    records: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["requests"]
    return records


def is_current(record: dict[str, Any], request: RequestInputs) -> bool:
    return (
        record.get("status") == "checked"
        and record.get("brief_sha256") == brief_sha256(request.brief)
        and record.get("claims_sha256") == claims_sha256(request.claims)
    )


def stale_requests(records: dict[str, dict[str, Any]]) -> list[str]:
    """Requests with no record, or a record made from other inputs than the test set holds now."""
    return [
        rid
        for rid, request in request_inputs().items()
        if rid not in records or not is_current(records[rid], request)
    ]


def for_request(
    request_id: str, records: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The record a run of this request starts with (`Pipeline.start(brief_check=...)`)."""
    loaded = load() if records is None else records
    if request_id not in loaded:
        raise FrozenBriefChecksError(f"no brief check frozen for {request_id}")
    return dict(loaded[request_id])
