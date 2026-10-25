"""Freeze an exported label file (docs/evaluation-plan.md §7, decided 2026-10-18).

Validates the export from label.html against the pool it was made on — same pool hash, every
candidate judged, the labeller named — then copies it to `labels.json` beside the pool and writes
`labels.lock` with its SHA-256, the pool hash, the labeller, the export time and the freeze time.
**A frozen set is never overwritten**: labels are fixed before any ranker tuning, and a later
disagreement is reported, not corrected. Prints the labeller's self-consistency on the hidden
duplicates, which the thesis reports.

An export that is complete can still be invalid, and is refused (docs/evaluation-plan.md §7,
2026-10-25): a candidate under a designed-unanswerable query marked relevant; one label above 90%
of all judgments; a median interval between judgments under 2 seconds. There is no override. Every
refusal prints the same diagnostics, so the reader sees why.

Usage (from backend/):
  uv run python -m scripts.freeze_relevance_labels ~/Downloads/relevance-labels-....json \\
      --pool ../evaluation/relevance
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.evaluation.relevance import (
    diagnostics,
    resolve,
    self_consistency,
    validate_labels,
    validity_problems,
)


class NotFreezable(Exception):
    """The export cannot be frozen; nothing was written."""


def _validity(
    pool: Path, payload: dict[str, object], key: dict[str, object], labels: dict[str, object]
) -> tuple[list[str], dict[str, object] | str]:
    """(reasons to refuse, the diagnostics or why they are unavailable)."""
    queries_yaml = pool / "queries.yaml"
    if not queries_yaml.exists():
        return [f"{queries_yaml.name} is missing: unanswerable queries cannot be checked"], (
            "diagnostics unavailable: queries.yaml is missing"
        )
    rows = yaml.safe_load(queries_yaml.read_text(encoding="utf-8"))
    unanswerable = [r["id"] for r in rows if r["unanswerable"]]
    try:
        found = diagnostics(payload, key, labels, unanswerable)
    except (KeyError, TypeError, ValueError) as e:  # labels from another pool, or malformed
        return [], f"diagnostics unavailable: {type(e).__name__}: {e}"
    return validity_problems(found), found


def _refusal(incomplete: str, reasons: list[str], found: dict[str, object] | str) -> str:
    lines = [incomplete] if incomplete else []
    lines += [f"invalid: {r}" for r in reasons]
    body = found if isinstance(found, str) else json.dumps(found, indent=2)
    return "\n".join([*lines, "diagnostics:", body])


def freeze(export: Path, pool: Path) -> dict[str, object]:
    lock = pool / "labels.lock"
    if lock.exists():
        raise NotFreezable(f"{lock} exists: the labels are frozen and are never overwritten")
    payload = json.loads((pool / "pool_payload.json").read_text(encoding="utf-8"))
    key = json.loads((pool / "pool_key.json").read_text(encoding="utf-8"))
    raw = export.read_bytes()
    labels = json.loads(raw)
    problems = validate_labels(payload, labels)
    shown = ""
    if problems:
        shown = "; ".join(problems[:5]) + (
            f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        )
    reasons, found = _validity(pool, payload, key, labels)
    if problems or reasons:
        raise NotFreezable(_refusal(shown, reasons, found))
    consistency = self_consistency(resolve(key, labels))
    (pool / "labels.json").write_bytes(raw)
    record: dict[str, object] = {
        "labels_sha256": hashlib.sha256(raw).hexdigest(),
        "pool_sha256": payload["pool_sha256"],
        "labeller": labels["labeller"],
        "started_at": labels.get("started_at"),
        "exported_at": labels.get("exported_at"),
        "frozen_at": datetime.now(UTC).isoformat(),
        "self_consistency": {
            "candidates_judged_twice": consistency.pairs,
            "agreed": consistency.agreed,
            "agreement": consistency.agreement,
        },
    }
    lock.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path)
    parser.add_argument("--pool", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = freeze(args.export, args.pool)
    except NotFreezable as e:
        print(f"not frozen: {e}")
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
