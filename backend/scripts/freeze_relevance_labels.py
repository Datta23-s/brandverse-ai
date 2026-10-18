"""Freeze an exported label file (docs/evaluation-plan.md §7, decided 2026-10-18).

Validates the export from label.html against the pool it was made on — same pool hash, every
candidate judged, the labeller named — then copies it to `labels.json` beside the pool and writes
`labels.lock` with its SHA-256, the pool hash, the labeller, the export time and the freeze time.
**A frozen set is never overwritten**: labels are fixed before any ranker tuning, and a later
disagreement is reported, not corrected. Prints the labeller's self-consistency on the hidden
duplicates, which the thesis reports.

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

from app.evaluation.relevance import resolve, self_consistency, validate_labels


class NotFreezable(Exception):
    """The export cannot be frozen; nothing was written."""


def freeze(export: Path, pool: Path) -> dict[str, object]:
    lock = pool / "labels.lock"
    if lock.exists():
        raise NotFreezable(f"{lock} exists: the labels are frozen and are never overwritten")
    payload = json.loads((pool / "pool_payload.json").read_text(encoding="utf-8"))
    key = json.loads((pool / "pool_key.json").read_text(encoding="utf-8"))
    raw = export.read_bytes()
    labels = json.loads(raw)
    problems = validate_labels(payload, labels)
    if problems:
        shown = "; ".join(problems[:5]) + (
            f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        )
        raise NotFreezable(shown)
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
