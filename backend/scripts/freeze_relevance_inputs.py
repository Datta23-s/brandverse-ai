"""Freeze and hash the relevance set's inputs (docs/evaluation-plan.md §7).

    uv run python -m scripts.freeze_relevance_inputs queries --dir ../evaluation/relevance
    uv run python -m scripts.freeze_relevance_inputs corpus  --dir ../evaluation/relevance

`queries` derives `queries.yaml` from `queries.md` (the pool builder's input) and writes
`queries.lock`; `corpus` hashes `corpus.yaml` and writes `corpus.lock`. A lock records SHA-256s,
counts and the freeze time, and **an existing lock is never overwritten**: the queries are frozen
before the corpus is drafted, the corpus before the pool is built, so that neither can be changed
to suit the other. The pool builder refuses inputs that differ from their lock.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.evaluation.relevance import parse_queries_md, sha256_file


class AlreadyFrozen(Exception):
    """The lock exists; nothing was written."""


def _write_lock(lock: Path, record: dict[str, object]) -> dict[str, object]:
    if lock.exists():
        raise AlreadyFrozen(f"{lock} exists: frozen inputs are never re-frozen")
    record["frozen_at"] = datetime.now(UTC).isoformat()
    with lock.open("w", encoding="utf-8", newline="\n") as f:  # LF, as the repo stores it
        f.write(json.dumps(record, indent=2) + "\n")
    return record


def freeze_queries(directory: Path) -> dict[str, object]:
    lock = directory / "queries.lock"
    if lock.exists():
        raise AlreadyFrozen(f"{lock} exists: frozen inputs are never re-frozen")
    md, out = directory / "queries.md", directory / "queries.yaml"
    rows = parse_queries_md(md.read_text(encoding="utf-8"))
    with out.open("w", encoding="utf-8", newline="\n") as f:  # LF: the hash is of these bytes
        f.write(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True, width=100))
    return _write_lock(
        lock,
        {
            "queries": len(rows),
            "unanswerable": sum(1 for r in rows if r["unanswerable"]),
            "sha256": {"md": sha256_file(md), "yaml": sha256_file(out)},
        },
    )


def freeze_corpus(
    directory: Path, supersedes: Path | None = None, reason: str | None = None
) -> dict[str, object]:
    """Freeze `corpus.yaml`. A corpus that replaces an earlier frozen one says so in its lock,
    naming the earlier lock's hash and the reason: the first is kept, never overwritten."""
    corpus = directory / "corpus.yaml"
    items = yaml.safe_load(corpus.read_text(encoding="utf-8"))
    per_brand: dict[str, int] = {}
    for item in items:
        per_brand[item["brand"]] = per_brand.get(item["brand"], 0) + 1
    record: dict[str, object] = {
        "items": len(items),
        "per_brand": per_brand,
        "sha256": {"corpus": sha256_file(corpus)},
    }
    if supersedes is not None:
        if not reason:
            raise ValueError("a superseding corpus must say why")
        earlier = json.loads(supersedes.read_text(encoding="utf-8"))
        record["supersedes"] = {
            "corpus_sha256": earlier["sha256"]["corpus"],
            "frozen_at": earlier["frozen_at"],
            "reason": reason,
        }
    return _write_lock(directory / "corpus.lock", record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["queries", "corpus"])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--supersedes", type=Path, help="corpus: the earlier corpus.lock replaced")
    parser.add_argument("--reason", help="corpus: why it was replaced")
    args = parser.parse_args()
    try:
        record = (
            freeze_queries(args.dir)
            if args.what == "queries"
            else freeze_corpus(args.dir, args.supersedes, args.reason)
        )
    except AlreadyFrozen as e:
        print(f"not frozen: {e}")
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
