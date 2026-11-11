"""Blind presentation: what the approver and the raters see, and in what order (decision-log 2.60).

Built 11 November 2026. It was deferred twice as "needed only for 16 November, built after the
freeze" and then was not built at all until it was looked for; without it neither the approval
sittings nor the rating round can be run as registered.

TWO CONSUMERS, ONE RULE SET.

1. **The approval queue** (`docs/approval-rubric.md` section 2, registered): completed campaigns
   from every arm and brand enter ONE queue, in batches of at most 15 (the session cap).
   **CONSTRAINED randomisation, not plain:** a brand's campaign N and N+1 are never adjacent, in any
   arm, **including across a batch boundary** (the last of one batch and the first of the next count
   as adjacent). A shuffle that breaks it is REDRAWN. The seed and the resulting order are logged
   per batch, so the order is reproducible. *Why the constraint exists:* in the Proposed arm a
   brand's campaign N+1 is generated only after N is decided, so a queue in run order would show a
   brand's campaigns back to back and let the approver infer the arm from the sequence.

2. **The rating round** (decision-log 2.18 ruling 1): both raters score the same 45 campaigns,
   "blind to arm and interleaved". The registered constraint above is about campaign N and N+1 of a
   brand; it does NOT forbid the three ARMS OF ONE REQUEST being adjacent, and three variants of
   one brief in a row would let a rater infer that they are variants. That is an additional
   constraint, so it is a named option (`forbid_same_request_adjacent`), ON for the rating round and
   OFF for the approval queue, which keeps the approval queue exactly as registered. **The
   difference is deliberate and recorded, not assumed.**

BLINDNESS IS BY CONSTRUCTION, NOT BY DISCIPLINE. A presented item carries an opaque label and the
fields the rubric's dimensions need (the product, the stated audience, the objective, the copy) and
NOTHING ELSE: no arm, no depth, no strategy, no evidence pack, no declared gaps, no call counts.
`Presented.__slots__` is the whole of what a reader can see, so a field cannot leak by being added
to a dict somewhere; `blind_packet` is the only way to build one, and `tests/test_blind.py` asserts
that no arm name appears anywhere in a rendered packet.

The seed convention is the project's: `sha256("<seed>|<attempt>|<key>")`, no pseudo-random generator
and no library version, so any order can be recomputed by hand from the seed and the attempt number.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

SESSION_CAP = 15  # campaigns per sitting (approval rubric section 2)
MAX_REDRAWS = 500


@dataclass(frozen=True, slots=True)
class Campaign:
    """One campaign as the harness takes it in. `arm` is used ONLY to order and to record the key;
    it is never part of what is presented (`Presented` has no arm field)."""

    campaign_id: str
    request_id: str
    brand_id: str
    position: int
    arm: str
    product: str
    audience: str
    objective: str
    copy: Mapping[str, str]

    @property
    def key(self) -> str:
        return f"{self.request_id}:{self.arm}"


@dataclass(frozen=True, slots=True)
class Presented:
    """EXACTLY what a rater or the approver sees. There is no arm, depth, strategy, evidence,
    gap or cost field, and there is no room for one: this is the whole surface."""

    label: str
    product: str
    audience: str
    objective: str
    copy: Mapping[str, str]


def blind_packet(campaign: Campaign, label: str) -> Presented:
    return Presented(
        label=label,
        product=campaign.product,
        audience=campaign.audience,
        objective=campaign.objective,
        copy=dict(campaign.copy),
    )


def _key(seed: int, attempt: int, key: str) -> str:
    return hashlib.sha256(f"{seed}|{attempt}|{key}".encode()).hexdigest()


def _violates(
    left: Campaign,
    right: Campaign,
    *,
    forbid_same_request_adjacent: bool,
) -> bool:
    """Is this adjacent pair forbidden? The registered rule: the same brand at campaign N and N+1,
    in any arm. The option: the same request (its three arms) adjacent."""
    if left.brand_id == right.brand_id and abs(left.position - right.position) == 1:
        return True
    return bool(forbid_same_request_adjacent and left.request_id == right.request_id)


def violations(
    order: Sequence[Campaign], *, forbid_same_request_adjacent: bool = False
) -> list[tuple[str, str]]:
    """Every forbidden adjacency in a flat order, as (key, key) pairs. Batch boundaries are NOT
    special here: the order is checked flat, which is what makes the across-boundary rule hold."""
    return [
        (a.key, b.key)
        for a, b in zip(order, order[1:], strict=False)
        if _violates(a, b, forbid_same_request_adjacent=forbid_same_request_adjacent)
    ]


@dataclass(frozen=True, slots=True)
class Batch:
    number: int
    campaigns: tuple[Campaign, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(f"C{self.number:02d}-{n:02d}" for n in range(1, len(self.campaigns) + 1))

    def presented(self) -> list[Presented]:
        return [
            blind_packet(c, label) for c, label in zip(self.campaigns, self.labels, strict=True)
        ]


@dataclass(frozen=True, slots=True)
class Presentation:
    """The whole presentation, with everything needed to reproduce and to audit it."""

    seed: int
    attempt: int  # which redraw satisfied the constraints; 0 is the first
    batches: tuple[Batch, ...]
    forbid_same_request_adjacent: bool
    session_cap: int

    @property
    def order(self) -> list[Campaign]:
        return [c for b in self.batches for c in b.campaigns]

    def key_by_label(self) -> dict[str, str]:
        """label -> "<request>:<arm>". THE UNBLINDING KEY: it is written to the record so the scores
        can be joined back to the arms, and it is never shown to a rater."""
        return {
            label: campaign.key
            for b in self.batches
            for campaign, label in zip(b.campaigns, b.labels, strict=True)
        }

    def record(self) -> dict[str, Any]:
        """What is logged per batch (the rubric requires the seed AND the resulting order)."""
        return {
            "seed": self.seed,
            "attempt": self.attempt,
            "session_cap": self.session_cap,
            "forbid_same_request_adjacent": self.forbid_same_request_adjacent,
            "batches": [
                {
                    "number": b.number,
                    "labels": list(b.labels),
                    "order": [c.key for c in b.campaigns],
                }
                for b in self.batches
            ],
            "unblinding_key": self.key_by_label(),
            "violations": [
                list(v)
                for v in violations(
                    self.order, forbid_same_request_adjacent=self.forbid_same_request_adjacent
                )
            ],
        }


class CannotSatisfyConstraints(RuntimeError):
    """No redraw within the bound satisfied the constraints: the caller is told, never given a
    queue that quietly breaks the rule."""


def present(
    campaigns: Iterable[Campaign],
    *,
    seed: int,
    session_cap: int = SESSION_CAP,
    forbid_same_request_adjacent: bool = False,
    max_redraws: int = MAX_REDRAWS,
) -> Presentation:
    """Shuffle into batches of at most `session_cap`, redrawing until no adjacency is forbidden.

    Deterministic: attempt `n` orders by `sha256("<seed>|<n>|<request>:<arm>")`, so the order is
    reproducible from the seed and the attempt number alone, and the redraws are not a hidden
    source of variation."""
    items = list(campaigns)
    if not items:
        return Presentation(seed, 0, (), forbid_same_request_adjacent, session_cap)
    for attempt in range(max_redraws):
        order = sorted(items, key=lambda c: _key(seed, attempt, c.key))
        if not violations(order, forbid_same_request_adjacent=forbid_same_request_adjacent):
            batches = tuple(
                Batch(number, tuple(order[i : i + session_cap]))
                for number, i in enumerate(range(0, len(order), session_cap), start=1)
            )
            return Presentation(seed, attempt, batches, forbid_same_request_adjacent, session_cap)
    raise CannotSatisfyConstraints(
        f"no order without a forbidden adjacency in {max_redraws} redraws from seed {seed}: "
        f"{len(items)} campaigns. The constraint is not satisfiable by redrawing alone and the "
        "owner decides (relax it, or build the order constructively), rather than being handed a "
        "queue that breaks it."
    )


def render_sheet(presentation: Presentation) -> str:
    """The raters' packet as plain text: labels, the product, the audience, the objective, the copy.
    Nothing else is in `Presented`, so nothing else can be rendered."""
    out: list[str] = []
    for batch in presentation.batches:
        out.append(f"=== sitting {batch.number} ({len(batch.campaigns)} campaigns) ===")
        for item in batch.presented():
            out.append(f"\n-- {item.label} --")
            out.append(f"product : {item.product}")
            out.append(f"audience: {item.audience}")
            out.append(f"brief   : {item.objective}")
            for part, text in item.copy.items():
                out.append(f"{part}: {text}")
        out.append("")
    return "\n".join(out)


def presented_json(presentation: Presentation) -> str:
    """The packets as data, for a renderer. The unblinding key is NOT in here."""
    return json.dumps([asdict(p) for b in presentation.batches for p in b.presented()], indent=1)
