"""Build the blind relevance-labelling task (docs/evaluation-plan.md §7, decided 2026-10-18).

Reads the queries and the corpus, builds the fixed candidate pool per query (BM25 ∪ vector, see
app/evaluation/relevance.py) and writes, under the output directory:

- `label.html`  — the page the labeller opens locally (no server, no network). It embeds ONLY the
  opaque payload: query text, candidate text, random ids. No scores, ranks or retriever.
- `pool_payload.json` — that payload, for the freeze step.
- `pool_key.json` — the mapping from opaque ids back to queries and items, and which queries are
  the hidden duplicates. Never loaded by the page; the labeller does not open it.
- `pool.lock` — the payload's hash, seed and depth, so the pool cannot change under the labels.

Refuses to overwrite an existing pool.lock: once labelling has started, the pool is fixed.

Usage (from backend/):
  uv run python -m scripts.build_relevance_pool --queries ../evaluation/relevance/queries.yaml \\
      --corpus ../evaluation/relevance/corpus.yaml --out ../evaluation/relevance --seed 20261018
The vector half needs the local encoder (Memory.md §5): by default the pinned one from
config/models.yaml (`app.memory.embeddings:default_encoder`, which needs the optional `embeddings`
dependency group: `uv sync --group embeddings`); `--encoder module:function` names another.
pool.lock records the encoder's model and revision, since the pool depends on them.
"""

import argparse
import importlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

from app.evaluation.relevance import Item, Query, build_pool
from app.memory.embeddings import Encoder

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "evaluation" / "label_page.html"


def render_page(payload: Mapping[str, object]) -> str:
    """The page with the payload embedded. `</` is escaped so no text can close the script tag."""
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__PAYLOAD__", data)


def _rows(path: Path) -> list[tuple[str, str]]:
    return [
        (str(r["id"]), str(r["text"])) for r in yaml.safe_load(path.read_text(encoding="utf-8"))
    ]


def load_queries(path: Path) -> list[Query]:
    return [Query(i, t) for i, t in _rows(path)]


def load_items(path: Path) -> list[Item]:
    return [Item(i, t) for i, t in _rows(path)]


def write_task(
    out: Path,
    queries: list[Query],
    items: list[Item],
    encoder: Encoder,
    *,
    seed: int,
    depth: int = 15,
    duplicates: int = 5,
) -> dict[str, object]:
    lock = out / "pool.lock"
    if lock.exists():
        raise FileExistsError(f"{lock} exists: the pool is fixed once labelling starts")
    payload, key = build_pool(
        queries, items, encoder, depth=depth, duplicates=duplicates, seed=seed
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "pool_payload.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "pool_key.json").write_text(
        json.dumps(key, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "label.html").write_text(render_page(payload), encoding="utf-8")
    sittings = payload["queries"]
    if not isinstance(sittings, list):
        raise TypeError("payload has no query list")
    summary: dict[str, object] = {
        "pool_sha256": payload["pool_sha256"],
        "encoder": {"model": encoder.model, "version": encoder.version},
        "seed": seed,
        "depth": depth,
        "queries": len(queries),
        "sittings": len(sittings),
        "hidden_duplicates": duplicates,
        "judgments": sum(len(q["candidates"]) for q in sittings),
    }
    lock.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--depth", type=int, default=15)
    parser.add_argument(
        "--encoder",
        default="app.memory.embeddings:default_encoder",
        help="module:function returning an Encoder (default: the pinned local encoder)",
    )
    args = parser.parse_args()
    module, _, name = args.encoder.partition(":")
    encoder: Encoder = getattr(importlib.import_module(module), name)()
    summary = write_task(
        args.out,
        load_queries(args.queries),
        load_items(args.corpus),
        encoder,
        seed=args.seed,
        depth=args.depth,
    )
    print(json.dumps(summary, indent=2))
    print(f"open {args.out / 'label.html'} in a browser; do not open pool_key.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
