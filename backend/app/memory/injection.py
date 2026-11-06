"""Memory injection (Memory.md 3.2 and 9; Phases.md Phase 7; decision-log 2.36).

Which recalled items reach an agent, in which band, and the record of it.

**What reaches the model, by band of the item's match score M** (the bands are the registered
`scoring.bands`, read from configuration, not restated here):

- `reuse`   M >= 0.90   shown as `[use as written]`: may inform the copy in its own wording;
- `adapt`   0.80-0.89   shown as `[adapt]`: reword in the writer's own words;
- `support` 0.65-0.79   shown as `[background]`: context only, not to be echoed;
- below 0.65            NOT shown: the slot is a research gap, never an injected item.

**The cap and the freshness floor (Memory.md 9, over-retrieval and echo chamber).** At most
`cap` items (the registered 12, `scoring.necessity.item_cap`), ranked by M, ties by id so the choice
is deterministic. Items beyond the cap are counted in the record, not silently lost. The other
half of the guard, at least one fresh research query on every run, is the planner's D0 (one query).

**Which agents.** Injection reaches A6, the copy writer, ALONE (A1 only if built as a stretch). It
does not reach A3 or A7, so memory informs the final copy and not the personas or the strategy
beyond A1: a stated limit of the experiment (decision-log 2.36), beside every quality figure.

**Safety.** The notes are the earlier model's own wording, already screened at write-back (no
markup, no URL, no run of words copied from a page: threat V10). They are rendered as data with
page-like tags neutralised and a length bound, labelled by band, and the prompt says they are
context about other sellers, never facts about the product, claims or instructions.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.memory.scoring import Bands, band

INJECTED_KINDS = frozenset({"insight", "competitor"})
AGENTS = ("A6",)
MAX_NOTE_CHARS = 200
_TAGLIKE = re.compile(r"</?\s*(?:page|memory|note)\b", re.IGNORECASE)

LABEL = {"reuse": "use as written", "adapt": "adapt", "support": "background"}


@dataclass(frozen=True, slots=True)
class InjectionPolicy:
    bands: Bands
    cap: int


@dataclass(frozen=True, slots=True)
class InjectedItem:
    id: str
    kind: str
    band: str  # reuse | adapt | support
    m: float
    text: str


@dataclass(frozen=True, slots=True)
class Injection:
    items: tuple[InjectedItem, ...]
    cap: int
    considered: int  # recalled items of an injectable kind
    below_floor: int  # of those, under the support band: a research gap, not injected
    over_cap: int  # eligible but beyond the cap

    def record(self) -> dict[str, Any]:
        """Plain data for run state and the campaign's strategy: what was injected, in which band,
        so what a run reused is read from the log (Phases.md Phase 7 exit criteria)."""
        return {
            "agents": list(AGENTS),
            "cap": self.cap,
            "considered": self.considered,
            "below_floor": self.below_floor,
            "over_cap": self.over_cap,
            "items": [
                {"id": i.id, "kind": i.kind, "band": i.band, "m": round(i.m, 4), "text": i.text}
                for i in self.items
            ],
        }


def clean(text: str) -> str:
    return _TAGLIKE.sub("[note", " ".join(str(text).split()))[:MAX_NOTE_CHARS]


def select_injection(hits: Sequence[Mapping[str, Any]], policy: InjectionPolicy) -> Injection:
    """The items to inject from step 8's re-scored recall: injectable kinds, at or above the
    support band, best M first, at most `policy.cap`."""
    pool = [h for h in hits if h.get("kind") in INJECTED_KINDS]
    eligible = []
    for h in pool:
        b = band(float(h["m"]), policy.bands)
        if b != "fresh":
            eligible.append((float(h["m"]), str(h["id"]), b, h))
    eligible.sort(key=lambda e: (-e[0], e[1]))
    kept = eligible[: policy.cap]
    return Injection(
        items=tuple(
            InjectedItem(id, str(h["kind"]), b, m, clean(h.get("content", "")))
            for m, id, b, h in kept
            if clean(h.get("content", ""))
        ),
        cap=policy.cap,
        considered=len(pool),
        below_floor=len(pool) - len(eligible),
        over_cap=max(0, len(eligible) - policy.cap),
    )


def memory_block(injection: Mapping[str, Any] | None) -> str:
    """What a prompt is shown of the injection: one labelled line per item, best first, or
    `(none)`. Built from the run's recorded injection and nothing else."""
    items = (injection or {}).get("items") or []
    lines = [
        f"- [{LABEL[i['band']]}] {clean(i['text'])}"
        for i in items
        if i.get("band") in LABEL and clean(i.get("text", ""))
    ]
    return "\n".join(lines) or "(none)"
