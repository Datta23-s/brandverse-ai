"""Echo: how much a corpus repeats the words of the queries asked of it (evaluation-plan §7).

The first relevance corpus was drafted with the 50 queries in view; the second, blind, by a session
that had only the brief. Echo is the measurable difference that order could make: if a corpus was
shaped by the queries, its items share more of the queries' own words than a corpus written without
them. Both corpora are measured with the same definitions, fixed here before either was computed.

For each query, its **content terms** are its words minus `STOP` (function words and the words
that frame a question to a marketing team: "what do we say", "how do we talk"), each lightly
stemmed (a trailing plural "s" dropped). Then, per query and corpus:

- `pool_coverage`: over the query's pooled candidates (the union of BM25 and encoder top 15, the
  pool a labeller sees), the mean fraction of its content terms that a candidate contains.
- `pool_bigram`: over the same candidates, the mean fraction of the query's adjacent content-term
  pairs that appear adjacent, in order, in the candidate (a phrase echoed, not just words).
- `corpus_best`: over ALL items, the highest content-term coverage by any one item. This does not
  depend on any retriever or on how a pool was formed.

The pool metrics are partly mechanical (BM25 puts overlapping items in the pool) and are reported
for what a labeller sees; `corpus_best` is the retriever-free figure. Both corpora are written from
the same brief, so brand and product words are shared by both; the figure of interest is the
difference between corpora, with its interval, not either level.
"""

import random
import statistics
from collections.abc import Mapping, Sequence

from app.evaluation.relevance import Item, Query, tokens

STOP = frozenset(
    "a an the and or of to in on for with about at by from as is are was were be been it its that "
    "this these those than then so if not no do does did we our us you your i what whats how when "
    "where which who why can could should would will may might someone something people say said "
    "tell talk give use used sound handle answer position words word s".split()
)


def _stem(token: str) -> str:
    return (
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
    )


def content_terms(text: str) -> list[str]:
    """The text's content terms in order, stemmed (repeats kept: bigrams need the order)."""
    return [_stem(t) for t in tokens(text) if t not in STOP]


def coverage(query_terms: Sequence[str], doc_terms: Sequence[str]) -> float:
    wanted = set(query_terms)
    return len(wanted & set(doc_terms)) / len(wanted) if wanted else 0.0


def _adjacent(terms: Sequence[str]) -> set[tuple[str, str]]:
    return set(zip(terms, terms[1:], strict=False))


def bigram_overlap(query_terms: Sequence[str], doc_terms: Sequence[str]) -> float:
    """Fraction of the query's adjacent content-term pairs found adjacent in the document."""
    wanted = _adjacent(query_terms)
    return len(wanted & _adjacent(doc_terms)) / len(wanted) if wanted else 0.0


def echo_for_query(
    query: Query, items: Mapping[str, Item], pool: Sequence[str]
) -> dict[str, float]:
    q = content_terms(query.text)
    docs = {iid: content_terms(item.text) for iid, item in items.items()}
    pooled = [docs[i] for i in pool]
    return {
        "pool_coverage": statistics.fmean(coverage(q, d) for d in pooled),
        "pool_bigram": statistics.fmean(bigram_overlap(q, d) for d in pooled),
        "corpus_best": max(coverage(q, d) for d in docs.values()),
    }


def summarise(per_query: Mapping[str, Mapping[str, float]], ids: Sequence[str]) -> dict[str, float]:
    return {
        metric: statistics.fmean(per_query[q][metric] for q in ids)
        for metric in ("pool_coverage", "pool_bigram", "corpus_best")
    }


def paired_difference(
    first: Mapping[str, Mapping[str, float]],
    second: Mapping[str, Mapping[str, float]],
    ids: Sequence[str],
    metric: str,
    *,
    resamples: int = 10_000,
    seed: int,
) -> dict[str, float]:
    """Mean of (first − second) over the queries, with a 95% percentile bootstrap interval over
    queries (resampled with replacement, the same queries in both corpora)."""
    diffs = [first[q][metric] - second[q][metric] for q in ids]
    rng = random.Random(seed)  # noqa: S311 - a reproducible bootstrap, not a secret
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(resamples))
    return {
        "mean": statistics.fmean(diffs),
        "low": means[int(0.025 * resamples)],
        "high": means[int(0.975 * resamples) - 1],
    }
