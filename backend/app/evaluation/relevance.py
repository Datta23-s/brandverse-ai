"""The relevance set for Precision@10 (Memory.md §8; docs/evaluation-plan.md §7; 2026-10-18).

Labelled by hand, blind, by Kundan, before any ranker tuning. This module builds the labelling task
so the labels cannot be contaminated, and checks them once made:

- **Pool.** For each query, a fixed candidate pool: the union of the top `depth` items from a
  lexical ranker (BM25, implemented here, independent of the system) and a vector ranker (cosine
  over the local encoder's embeddings). Neither RRF nor the `M`-score — the ranker under test —
  shapes it.
- **Blind.** The page payload holds only opaque ids and text: no score, no rank, no retriever.
  Candidate order is shuffled per query, and query order too. The mapping from opaque ids back to
  queries and items — and which queries are duplicates — lives in a separate key file the page
  never loads.
- **Consistency.** `duplicates` queries appear twice under different opaque ids with a fresh
  shuffle; agreement between the two labellings is reported (`self_consistency`).
- **Frozen.** Exported labels are validated against the pool's hash and frozen
  (scripts/freeze_relevance_labels.py); a frozen set is never overwritten.

Precision@10 counts an item outside the judged pool as not relevant and reports how many there
were, so pool depth is visible rather than hidden.
"""

import hashlib
import json
import math
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Literal

# Queries and items are embedded by separate methods: the query instruction applies to queries
# only (Memory.md §5).
from app.memory.embeddings import Encoder

Judgment = Literal["relevant", "partial", "not_relevant"]
JUDGMENTS: tuple[Judgment, ...] = ("relevant", "partial", "not_relevant")

# Shown in the task itself, so judgments stay consistent across a two-hour sitting.
DEFINITIONS: Mapping[Judgment, str] = {
    "relevant": "The item directly answers what the query asks, and a marketer could use it for "
    "that query as it stands.",
    "partial": "The item is on the query's topic but answers only part of it, is too general, or "
    "would need real adaptation to use.",
    "not_relevant": "The item does not help answer the query.",
}


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class Query:
    id: str
    text: str


# ── Lexical ranker: BM25 (k1 = 1.5, b = 0.75), self-contained ────────────────────────────────

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def bm25_rank(query: str, items: Sequence[Item], k1: float = 1.5, b: float = 0.75) -> list[str]:
    """Item ids by BM25 score, best first; items sharing no term with the query are left out."""
    docs = [tokens(i.text) for i in items]
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n
    df: Counter[str] = Counter(t for d in docs for t in set(d))
    q = set(tokens(query))
    scored = []
    for item, doc in zip(items, docs, strict=True):
        tf = Counter(doc)
        score = 0.0
        for t in q & tf.keys():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(doc) / avgdl))
        if score > 0:
            scored.append((score, item.id))
    return [i for _, i in sorted(scored, key=lambda s: (-s[0], s[1]))]


def vector_rank(
    query_vector: Sequence[float], item_vectors: Mapping[str, Sequence[float]]
) -> list[str]:
    """Item ids by cosine similarity, best first."""

    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
        return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0

    scored = [(cosine(query_vector, v), i) for i, v in item_vectors.items()]
    return [i for _, i in sorted(scored, key=lambda s: (-s[0], s[1]))]


# ── The pool ─────────────────────────────────────────────────────────────────────────────────


def _opaque(seed: int, kind: str, n: int) -> str:
    return hashlib.sha256(f"{seed}:{kind}:{n}".encode()).hexdigest()[:10]


def candidate_pools(
    queries: Sequence[Query], items: Sequence[Item], encoder: Encoder, depth: int
) -> dict[str, list[str]]:
    """Per query, the union of the top `depth` of BM25 and of cosine over the encoder's vectors,
    as a sorted list of item ids: a set, since order is decided by the shuffle in `build_pool`,
    not by a ranker."""
    vectors = dict(
        zip((i.id for i in items), encoder.embed_documents([i.text for i in items]), strict=True)
    )
    query_vectors = encoder.embed_queries([q.text for q in queries])
    pools: dict[str, list[str]] = {}
    for q, qv in zip(queries, query_vectors, strict=True):
        union = dict.fromkeys(bm25_rank(q.text, items)[:depth] + vector_rank(qv, vectors)[:depth])
        pools[q.id] = sorted(union)
    return pools


def build_pool(
    queries: Sequence[Query],
    items: Sequence[Item],
    encoder: Encoder,
    *,
    depth: int = 15,
    duplicates: int = 5,
    seed: int,
) -> tuple[dict[str, object], dict[str, object]]:
    """(payload, key). The payload is all the page sees; the key maps it back and stays out of
    the page. Deterministic for a given seed."""
    if len({q.id for q in queries}) != len(queries) or len({i.id for i in items}) != len(items):
        raise ValueError("query and item ids must be unique")
    if not 0 <= duplicates <= len(queries):
        raise ValueError("cannot duplicate more queries than there are")
    rng = random.Random(seed)  # noqa: S311 - a reproducible shuffle, not a secret
    text = {i.id: i.text for i in items}
    pools = candidate_pools(queries, items, encoder, depth)

    repeated = rng.sample([q.id for q in queries], duplicates)
    sittings = [(q.id, False) for q in queries] + [(qid, True) for qid in repeated]
    rng.shuffle(sittings)

    item_code = {iid: _opaque(seed, "item", n) for n, iid in enumerate(sorted(text))}
    query_text = {q.id: q.text for q in queries}
    payload_queries, key_queries = [], {}
    for n, (qid, is_duplicate) in enumerate(sittings):
        code = _opaque(seed, "query", n)
        candidates = list(pools[qid])
        rng.shuffle(candidates)  # a fresh order for every sitting, duplicates included
        payload_queries.append(
            {
                "id": code,
                "text": query_text[qid],
                "candidates": [{"id": item_code[i], "text": text[i]} for i in candidates],
            }
        )
        key_queries[code] = {"query": qid, "duplicate": is_duplicate}
    payload: dict[str, object] = {
        "definitions": dict(DEFINITIONS),
        "queries": payload_queries,
    }
    payload["pool_sha256"] = pool_hash(payload)
    key: dict[str, object] = {
        "pool_sha256": payload["pool_sha256"],
        "seed": seed,
        "depth": depth,
        "queries": key_queries,
        "items": {code: iid for iid, code in item_code.items()},
    }
    return payload, key


def pool_hash(payload: Mapping[str, object]) -> str:
    body = {k: v for k, v in payload.items() if k != "pool_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ── Labels ───────────────────────────────────────────────────────────────────────────────────


def validate_labels(payload: Mapping[str, object], labels: Mapping[str, object]) -> list[str]:
    """Problems with an exported label file; empty when it can be frozen."""
    problems = []
    if labels.get("pool_sha256") != payload.get("pool_sha256"):
        problems.append("labels were made on a different pool")
    if not str(labels.get("labeller", "")).strip():
        problems.append("labeller is not named")
    judged = labels.get("labels")
    if not isinstance(judged, Mapping):
        return [*problems, "no labels"]
    queries = payload["queries"]
    if not isinstance(queries, list):
        raise TypeError("payload has no query list")
    for q in queries:
        got = judged.get(q["id"], {})
        for c in q["candidates"]:
            mark = got.get(c["id"]) if isinstance(got, Mapping) else None
            value = mark.get("judgment") if isinstance(mark, Mapping) else None
            if value not in JUDGMENTS:
                problems.append(f"query {q['id']}: candidate {c['id']} is not judged")
    return problems


def resolve(
    key: Mapping[str, object], labels: Mapping[str, object]
) -> dict[tuple[str, str], list[Judgment]]:
    """(query id, item id) -> the judgment(s) given, duplicates giving two."""
    queries, items, judged = key["queries"], key["items"], labels["labels"]
    if not (
        isinstance(queries, Mapping) and isinstance(items, Mapping) and isinstance(judged, Mapping)
    ):
        raise TypeError("key or labels are malformed")
    out: dict[tuple[str, str], list[Judgment]] = {}
    for code, marks in judged.items():
        qid = queries[code]["query"]
        for item_code, mark in marks.items():
            out.setdefault((qid, items[item_code]), []).append(mark["judgment"])
    return out


@dataclass(frozen=True, slots=True)
class Consistency:
    pairs: int  # candidates judged twice (in duplicate queries)
    agreed: int

    @property
    def agreement(self) -> float:
        return self.agreed / self.pairs if self.pairs else float("nan")


def self_consistency(resolved: Mapping[tuple[str, str], Sequence[Judgment]]) -> Consistency:
    twice = [js for js in resolved.values() if len(js) == 2]
    return Consistency(len(twice), sum(1 for a, b in twice if a == b))


# ── Validity: an export that is complete can still be unreadable as a set of judgments ───────
# Decided 2026-10-25 after a sitting whose export was discarded (docs/evaluation-plan.md §7). These
# are hard refusals with no override: a legitimate export that trips one is a conversation.

MAX_SINGLE_LABEL_SHARE = 0.90
MIN_MEDIAN_INTERVAL_SECONDS = 2.0


def _percentile(sorted_values: Sequence[float], p: float) -> float:
    return sorted_values[int(p * (len(sorted_values) - 1))]


def diagnostics(
    payload: Mapping[str, object],
    key: Mapping[str, object],
    labels: Mapping[str, object],
    unanswerable: Sequence[str],
) -> dict[str, object]:
    """The five facts a refusal shows, computed on whatever was judged (partial exports included):
    distribution, the unanswerable queries' labels, which sittings are complete, the interval
    distribution, and the hidden repeats' agreement. Intervals are the gaps between consecutive
    judgments over the whole session, ordered by timestamp."""
    queries, judged = key["queries"], labels.get("labels")
    pool = payload["queries"]
    if not (
        isinstance(queries, Mapping) and isinstance(judged, Mapping) and isinstance(pool, list)
    ):
        raise TypeError("payload, key or labels are malformed")
    marks = [
        (code, cid, m)
        for code, cs in judged.items()
        for cid, m in cs.items()
        if isinstance(m, Mapping)
    ]
    n = len(marks)
    counts = Counter(m.get("judgment") for _, _, m in marks)
    distribution = {
        j: {"count": counts[j], "share": counts[j] / n if n else float("nan")} for j in JUDGMENTS
    }

    def name(code: str) -> str:
        return str(queries[code]["query"]) + (" (repeat)" if queries[code]["duplicate"] else "")

    complete, partial, untouched = [], [], []
    for q in pool:
        total = len(q["candidates"])
        done = sum(1 for c in q["candidates"] if c["id"] in judged.get(q["id"], {}))
        if done == total:
            complete.append(name(q["id"]))
        elif done == 0:
            untouched.append(name(q["id"]))
        else:
            partial.append(f"{name(q['id'])} ({done}/{total})")
    unanswerable_labels = {
        name(code): dict(Counter(m.get("judgment") for m in judged[code].values()))
        for code in sorted(judged, key=name)
        if queries[code]["query"] in unanswerable
    }
    stamps = sorted(datetime.fromisoformat(str(m["at"])) for _, _, m in marks)
    gaps = sorted((b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False))
    intervals: dict[str, object] = {"n": len(gaps)}
    if gaps:
        intervals |= {
            "min": gaps[0], "p10": _percentile(gaps, 0.1), "p25": _percentile(gaps, 0.25),
            "median": median(gaps), "p75": _percentile(gaps, 0.75), "p90": _percentile(gaps, 0.9),
            "p99": _percentile(gaps, 0.99), "max": gaps[-1],
            "share_at_or_under_1s": sum(1 for g in gaps if g <= 1) / len(gaps),
        }  # fmt: skip
    consistency = self_consistency(resolve(key, labels))
    return {
        "judged": n,
        "total": sum(len(q["candidates"]) for q in pool),
        "distribution": distribution,
        "unanswerable": unanswerable_labels,
        "sittings": {
            "complete": sorted(complete),
            "partial": sorted(partial),
            "untouched": sorted(untouched),
        },
        "intervals_seconds": intervals,
        "hidden_repeats": {
            "pairs": consistency.pairs,
            "agreed": consistency.agreed,
            "note": "a uniform label set agrees with itself perfectly; read with the distribution",
        },
    }  # fmt: skip


def validity_problems(found: Mapping[str, object]) -> list[str]:
    """Reasons an export must not be frozen, from `diagnostics`. Empty when none apply."""
    problems = []
    unanswerable = found["unanswerable"]
    if not isinstance(unanswerable, Mapping):
        raise TypeError("malformed diagnostics")
    flagged = {q: c["relevant"] for q, c in unanswerable.items() if c.get("relevant")}
    if flagged:
        problems.append(
            "candidates under designed-unanswerable queries are marked relevant "
            f"({sum(flagged.values())} across {', '.join(sorted(flagged))}): "
            "nothing in the corpus answers them"
        )
    distribution = found["distribution"]
    if not isinstance(distribution, Mapping):
        raise TypeError("malformed diagnostics")
    for j, d in distribution.items():
        if d["count"] and d["share"] > MAX_SINGLE_LABEL_SHARE:
            problems.append(
                f"{d['share']:.1%} of judgments are '{j}' (limit {MAX_SINGLE_LABEL_SHARE:.0%}): "
                "a pool built to contain non-matches was not read"
            )
    intervals = found["intervals_seconds"]
    if not isinstance(intervals, Mapping):
        raise TypeError("malformed diagnostics")
    if "median" in intervals and intervals["median"] < MIN_MEDIAN_INTERVAL_SECONDS:
        problems.append(
            f"median interval between judgments is {intervals['median']:.2f}s "
            f"(minimum {MIN_MEDIAN_INTERVAL_SECONDS:g}s): too fast to have read candidate and query"
        )
    return problems


def precision_at_10(
    ranking: Sequence[str], judgments: Mapping[str, Judgment], *, partial_counts: bool = False
) -> tuple[float, int]:
    """(precision, unjudged): relevant items in the top 10 over 10. An item outside the judged
    pool counts as not relevant, and how many there were is returned beside the figure."""
    top = list(ranking)[:10]
    if not top:
        return float("nan"), 0
    hits = {"relevant"} | ({"partial"} if partial_counts else set())
    unjudged = sum(1 for i in top if i not in judgments)
    return sum(1 for i in top if judgments.get(i) in hits) / 10, unjudged


# ── Frozen inputs: the queries and the corpus ────────────────────────────────────────────────

_BRAND_OF_HEADING = {
    "METTLEPAN": "mettlepan",
    "ZARVIK NUTRITION": "zarvik",
    "NOOKWELL HOME": "nookwell",
    "UNANSWERABLE": None,
}
_QUERY_LINE = re.compile(r"^(\d+)\.\s+(.+?)(\s+\[UNANSWERABLE\])?\s*$")


def parse_queries_md(text: str) -> list[dict[str, object]]:
    """`queries.md` -> the rows of `queries.yaml`: id, brand, text, unanswerable. The marker stays
    out of `text` (the labeller's page shows the query only). Refuses a file whose numbering is not
    1..N, or whose markers disagree with its UNANSWERABLE section."""
    rows: list[dict[str, object]] = []
    section: str | None = None
    in_section = False
    for line in text.splitlines():
        heading = line.strip()
        if heading in _BRAND_OF_HEADING:
            section, in_section = _BRAND_OF_HEADING[heading], True
            continue
        m = _QUERY_LINE.match(line)
        if not m:
            continue
        if not in_section:
            raise ValueError(f"query before any brand heading: {line!r}")
        marked = m.group(3) is not None
        if marked != (section is None):
            raise ValueError(f"query {m.group(1)} disagrees with its section about UNANSWERABLE")
        rows.append(
            {
                "id": f"q{int(m.group(1)):02d}",
                "brand": section,
                "text": m.group(2),
                "unanswerable": marked,
            }
        )
    if [r["id"] for r in rows] != [f"q{n:02d}" for n in range(1, len(rows) + 1)]:
        raise ValueError("queries are not numbered 1..N in order")
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen(lock: Path, **files: Path) -> None:
    """Raise unless `lock` exists and records each named file's current SHA-256. The pool is built
    only from inputs that were frozen and have not changed since."""
    if not lock.exists():
        raise FileNotFoundError(f"{lock} is missing: the inputs have not been frozen")
    recorded = json.loads(lock.read_text(encoding="utf-8"))["sha256"]
    for name, path in files.items():
        if recorded.get(name) != sha256_file(path):
            raise ValueError(f"{path.name} differs from what {lock.name} froze")
