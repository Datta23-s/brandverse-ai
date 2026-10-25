"""Source scoring (Architecture.md §6.2; Phases.md Phase 5): how far one web page is trusted as
evidence, in [0, 1], and whether it is kept at all. Standalone: nothing calls it yet, and nothing is
stored (the migration, `app.match_memory` and the Phase 6 caller come after; Memory.md §3.1).

Three factors, weighted from `config/scoring.yaml` `source:`; every number there is an untuned
default and a parameter of the results:
- **authority**: a prior about the kind of publisher, from the host alone (the longest matching
  suffix in a configured class, else `unknown`). It is not a judgement that the page is right, and
  it cannot tell a manufacturer's own site from an independent one;
- **recency**: 0.5 ** (age / half-life) from the page's own stated date. A page that states none has
  no recency: the factor is left out and the weights renormalised, never filled with a guess;
- **specificity**: the share of the query's distinct content words found, as whole words, in the
  page's title and text. A query with no content words has none, and it is left out likewise.

**Corroboration is not modelled** (decided 2026-10-25, Memory.md §3.1): a bonus for several
independent sources would be a constant with no principled value. It is future work.

A page scoring below `accept_at_or_above` is not accepted: it is discarded before it reaches an
agent. Unscored sources are how hallucinated authority enters a system.

**An item backed by several pages takes the maximum** (`evidence_score`), because adding a source
must never lower an item's evidential score; the mean and the minimum both can.
"""

from collections.abc import Iterable
from datetime import date
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from app.core.text_match import normalise
from app.memory.scoring import SourceScoring
from app.research.extraction import ExtractedPage

Factor = str  # "authority" | "recency" | "specificity"


class SourceScore(BaseModel):
    """The score and what it was made of, so a reported figure always says how it arose."""

    model_config = ConfigDict(frozen=True)

    url: str
    value: float
    components: dict[Factor, float]
    left_out: tuple[Factor, ...]
    authority_class: str
    accepted: bool


def authority(url: str, cfg: SourceScoring) -> tuple[float, str]:
    """(score, class name) for the URL's host: the longest configured suffix that matches on a
    label boundary (`fssai.gov.in` matches `gov.in`; `fakegov.in` does not)."""
    host = (urlsplit(url).hostname or "").casefold()
    best: tuple[int, float, str] | None = None
    for cls in cfg.authority.classes:
        for suffix in cls.suffixes:
            if (host == suffix or host.endswith("." + suffix)) and (
                best is None or len(suffix) > best[0]
            ):
                best = (len(suffix), cls.score, cls.name)
    if best is None:
        return cfg.authority.unknown, "unknown"
    return best[1], best[2]


def recency(published: date | None, today: date, cfg: SourceScoring) -> float | None:
    if published is None:
        return None
    age_days = max(0, (today - published).days)  # a date in the future is not older than today
    return float(0.5 ** (age_days / cfg.recency_half_life_days))


def specificity(query: str, title: str | None, text: str, cfg: SourceScoring) -> float | None:
    ignored = set(cfg.specificity_ignore_words)
    wanted = {
        w
        for w in normalise(query).split()
        if len(w) >= cfg.specificity_min_word_chars and w not in ignored
    }
    if not wanted:
        return None
    present = set(normalise(f"{title or ''} {text}").split())
    return len(wanted & present) / len(wanted)


def score_source(
    page: ExtractedPage, query: str, *, today: date, cfg: SourceScoring
) -> SourceScore:
    authority_score, authority_class = authority(page.url, cfg)
    values: dict[Factor, float | None] = {
        "authority": authority_score,
        "recency": recency(page.published, today, cfg),
        "specificity": specificity(query, page.title, page.text, cfg),
    }
    weights = cfg.weights.model_dump()
    present = {k: v for k, v in values.items() if v is not None}
    total = sum(weights[k] for k in present)
    if total == 0:
        raise ValueError("no source-score factor could be computed")
    value = sum(weights[k] * v for k, v in present.items()) / total
    return SourceScore(
        url=page.url,
        value=value,
        components=present,
        left_out=tuple(k for k, v in values.items() if v is None),
        authority_class=authority_class,
        accepted=value >= cfg.accept_at_or_above,
    )


def evidence_score(scores: Iterable[float]) -> float | None:
    """The score an item backed by these sources carries: the maximum, None if it has none.
    Monotone: adding a source never lowers it."""
    return max(scores, default=None)
