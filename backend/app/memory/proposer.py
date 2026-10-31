"""The deterministic proposer: which facts a run offers memory at its end (Memory.md 6.1; V13).

Until this existed, `writeback_step` passed `candidates=[]`: no real run wrote any memory item, so
campaigns 2 and 3 of a brand saw nothing from campaign 1 and the Proposed arm was a no-memory arm.
This selects among things the run already holds; it makes no model call and no judgement, so the
same state gives the same proposal (Memory.md 6.1 leaves "the agent or step that proposes it"
open; the owner ruled 2026-10-31 that it is deterministic: a model proposer would add a call per
run, a failure mode, and nondeterminism to the one mechanism the thesis is about).

**What it proposes, and only this.** A2's quote-verified category conventions (`insight`) and
competitor messages (`competitor`), each already reduced to its own derived wording by A2 and each
tied to the scored page it came from. A candidate carries that page as its evidence, so the item
is written with `sources` rows, a `source_score` and so an `E`: **only item kinds that can carry
the full component set are created.** Anything that cannot is not proposed, and the reason is
recorded (`Skipped`), never silent:

- **claim items are not created.** A claim's evidence page (usually the brand's own) is neither
  fetched nor scored by the pipeline, so a claim item would have no `E` while an insight item has
  one, and one reuse band would then be applied to two scores that mean different things, invisibly.
  The claim already lives in `product_claims`, a stronger record than a memory copy of it.
- persona items are not created (personas travel as persona rows on every item, migration 0037);
- A1's positioning is not proposed: its basis is verified product claims, whose pages are not
  scored, so it would have no `E` either (an owner ruling on a new `strategy` kind is pending);
- A2's whitespace is never proposed: it is an inference with no verified quote (Memory.md 6.2).

Eligibility beyond this is `stage_writeback`'s: an approved campaign, a gate report that did not
block, the screens for ephemera and personal data, one fact, a confidence above the revocation
threshold.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.memory.scoring import Writeback
from app.memory.writeback import Candidate, EvidenceSource, evidence_from_finding


@dataclass(frozen=True, slots=True)
class Skipped:
    kind: str
    content: str
    reason: str

    def record(self) -> dict[str, str]:
        return {"kind": self.kind, "content": self.content, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class Proposal:
    candidates: tuple[Candidate, ...]
    skipped: tuple[Skipped, ...]


def propose(state: Mapping[str, Any], cfg: Writeback, config_version: str) -> Proposal:
    """The candidates a run offers memory, in a fixed order (A2's own order, conventions then
    competitors), and what it declined to offer with why."""
    market = (state.get("agent_outputs") or {}).get("A2") or {}
    by_url = {f["url"]: f for f in state.get("findings", []) if f.get("url")}
    candidates: list[Candidate] = []
    skipped: list[Skipped] = []
    seen: set[tuple[str, str]] = set()
    for fact in market.get("memory_facts") or []:
        kind, content = fact["kind"], " ".join(str(fact["text"]).split())
        if (kind, content) in seen:
            continue
        seen.add((kind, content))
        finding = by_url.get(fact["source_url"])
        if finding is None:
            skipped.append(
                Skipped(kind, content, "its source page is not among the run's findings")
            )
            continue
        try:
            evidence: EvidenceSource = evidence_from_finding(finding, config_version)
        except (KeyError, ValueError) as e:
            skipped.append(Skipped(kind, content, f"its source cannot be recorded: {e!r}"))
            continue
        candidates.append(
            Candidate(
                kind=kind,
                content=content,
                claim_id=None,
                confidence=cfg.evidence_item_confidence,
                freshness_days=cfg.typical_freshness_days.get(kind),
                evidence=(evidence,),
            )
        )
    return Proposal(tuple(candidates), tuple(skipped))
