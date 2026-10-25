"""Measure the echo of two corpora against the frozen queries (app/evaluation/echo.py).

Usage (from backend/):
  uv run python -m scripts.measure_echo --queries ../evaluation/relevance/queries.yaml \\
      --first ../evaluation/relevance/superseded/reversed-order/corpus.yaml \\
      --second ../evaluation/relevance/corpus.yaml --out ../evaluation/relevance/echo.json

Pools are rebuilt exactly as the labelling task builds them (BM25 and the pinned encoder, top 15).
Writes the figures and refuses to overwrite an existing file: the result is recorded once.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from app.evaluation.echo import STOP, echo_for_query, paired_difference, summarise
from app.evaluation.relevance import Item, Query, candidate_pools
from app.memory.embeddings import default_encoder

SEED = 20261025


def measure(corpus: Path, queries: list[Query], encoder: object) -> dict[str, dict[str, float]]:
    rows = yaml.safe_load(corpus.read_text(encoding="utf-8"))
    items = {str(r["id"]): Item(str(r["id"]), str(r["text"])) for r in rows}
    pools = candidate_pools(queries, list(items.values()), encoder, 15)  # type: ignore[arg-type]
    return {q.id: echo_for_query(q, items, pools[q.id]) for q in queries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument(
        "--first", type=Path, required=True, help="the corpus drafted with the queries"
    )
    parser.add_argument("--second", type=Path, required=True, help="the corpus drafted blind")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        print(f"{args.out} exists: the echo is recorded once")
        return 1
    rows = yaml.safe_load(args.queries.read_text(encoding="utf-8"))
    queries = [Query(r["id"], r["text"]) for r in rows]
    answerable = [r["id"] for r in rows if not r["unanswerable"]]
    unanswerable = [r["id"] for r in rows if r["unanswerable"]]
    encoder = default_encoder()
    first, second = (measure(c, queries, encoder) for c in (args.first, args.second))
    result = {
        "definitions": "app/evaluation/echo.py",
        "stop_words": sorted(STOP),
        "queries": {"answerable": len(answerable), "unanswerable": len(unanswerable)},
        "first_corpus_drafted_with_queries_in_view": {
            "answerable": summarise(first, answerable),
            "unanswerable": summarise(first, unanswerable),
        },
        "second_corpus_drafted_blind": {
            "answerable": summarise(second, answerable),
            "unanswerable": summarise(second, unanswerable),
        },
        "first_minus_second_answerable": {
            m: paired_difference(first, second, answerable, m, seed=SEED)
            for m in ("pool_coverage", "pool_bigram", "corpus_best")
        },
        "per_query": {"first": first, "second": second},
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "per_query"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
