"""The evaluation's generation run: resumable, checkpointed per campaign (decision-log 2.48).

About 350 calls over four hours unattended on a machine that has lost days to power, DNS and the
provider: if this dies at campaign 40 of 60 it continues from 41. The mechanism, not a promise:

* **An append-only checkpoint file** (JSON lines, flushed and fsynced per line). The record of what
  happened is written BEFORE the next side effect that depends on it: an `intent` line before the
  run is started, a `start` line once it has an id, an `end` line when it has an outcome.
* **Resume = read the file.** An item with a `done` outcome is skipped. An item with a `start` and
  no `end` (the process died while it was in flight) is RECONCILED against the run's real status:
  if it reached an outcome the outcome is recorded and the item is not repeated; if it did not, the
  attempt is recorded as `interrupted` and the item is started again. An `intent` with no `start`
  (killed between "decide to start" and "the id came back") is searched for an orphan run before
  anything is started again, so a kill cannot create a second run silently.
* **An interrupted attempt is NOT an infrastructure failure.** The re-run cap (2, `reruns.py`) is
  for the provider's outages; a killed harness is not an outage and must not use the cap up. It is
  recorded, listed in the report, and its calls stay in the ledger.
* **An infrastructure failure is a re-run, not a result** (`reruns.next_action`): the run ended
  `invalidated` and nobody read its copy. The third failure ESCALATES: the batch stops and the owner
  is told, it is not re-run.

The order is the registered one (`evaluation/run_order.yaml`, `schedule.py`): this module filters
it into WAVES and never reorders it. The Baselines never read or write memory, so all 60 can be
generated in one wave. The Proposed arm's campaign N+1 reads the memory that campaign N's APPROVAL
writes (Rules.md T6), and the approver is a human, so it is generated in three waves, each refused
until the previous position is decided: that gate is `Runner.decided`, checked here, not left to
the person typing the command.
"""

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from app.evaluation.reruns import MAX_RERUNS, Attempt, next_action

BASELINE_ARMS = ("baseline_a", "baseline_b")
Wave = Literal["baselines", "proposed:1", "proposed:2", "proposed:3"]
WAVES: tuple[Wave, ...] = ("baselines", "proposed:1", "proposed:2", "proposed:3")

# What a run reached, as the harness sees it.
#   parked       the run is waiting for a human decision (the normal end of generation)
#   ended        the run finished without parking, and not by infrastructure failure (partial,
#                blocked, a budget stop): a RESULT, recorded, not re-run
#   invalidated  an infrastructure failure ended it before any human saw it: re-run (capped)
#   running      still in flight (or its worker is gone): only ever seen by reconciliation
Outcome = Literal["parked", "ended", "invalidated", "running"]


class GenerationStopped(RuntimeError):
    """The batch stops and the owner decides: an escalation, a refused wave, a budget refusal."""


@dataclass(frozen=True, slots=True)
class Item:
    request_id: str
    arm: str
    position: int  # the campaign's position in its brand (1, 2, 3)

    @property
    def key(self) -> str:
        return f"{self.request_id}:{self.arm}"


class Runner(Protocol):
    """The I/O the driver needs: the API and the database for real, a fake in tests."""

    async def start(self, item: Item) -> str:
        """Start the run. Returns its id."""

    async def find_orphan(self, item: Item, since: datetime) -> str | None:
        """The id of a run for this item started at or after `since` that the checkpoint never
        heard about (killed between the decision to start and the id coming back), else None."""

    async def wait(self, run_id: str) -> tuple[Outcome, str | None]:
        """Wait until the run parks or ends. Returns (outcome, infrastructure_failure kind)."""

    async def status(self, run_id: str) -> tuple[Outcome, str | None]:
        """The run's state right now, without waiting. `running` if it is not at an outcome."""

    async def decided(self, arm: str, position: int) -> bool:
        """Has EVERY run of this arm at this position had its human decision?"""


def wave_items(
    order: Iterable[tuple[str, str]], positions: dict[str, int], wave: Wave
) -> list[Item]:
    """The registered order filtered to one wave. Never reordered: the filter keeps the sequence."""
    if wave not in WAVES:
        raise ValueError(f"unknown wave {wave!r}; waves are {', '.join(WAVES)}")
    items = [Item(rid, arm, positions[rid]) for rid, arm in order]
    if wave == "baselines":
        return [i for i in items if i.arm in BASELINE_ARMS]
    position = int(wave.split(":")[1])
    return [i for i in items if i.arm == "proposed" and i.position == position]


# ── The checkpoint ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Attempted:
    """One attempt at an item, as the checkpoint reconstructs it."""

    number: int
    run_id: str | None = None
    intent_at: str | None = None
    outcome: str | None = None  # parked | ended | invalidated | interrupted
    infrastructure_failure: str | None = None


@dataclass(slots=True)
class ItemState:
    attempts: list[Attempted] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return any(a.outcome in ("parked", "ended") for a in self.attempts)

    @property
    def open_attempt(self) -> Attempted | None:
        last = self.attempts[-1] if self.attempts else None
        return last if last is not None and last.outcome is None else None

    def counted(self) -> list[Attempt]:
        """The attempts the re-run cap counts. `interrupted` (a harness kill) is not one."""
        return [
            Attempt(a.number, a.infrastructure_failure if a.outcome == "invalidated" else None)
            for a in self.attempts
            if a.outcome in ("parked", "ended", "invalidated")
        ]


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), **event})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        lines = self.path.read_text(encoding="utf-8").split("\n")
        for n, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                if n == len(lines) - 1 or not any(x.strip() for x in lines[n + 1 :]):
                    break  # a line the kill cut off: it never happened
                raise
        return out

    def state(self) -> dict[str, ItemState]:
        states: dict[str, ItemState] = {}
        for e in self.events():
            st = states.setdefault(e["item"], ItemState())
            kind = e["event"]
            if kind == "intent":
                st.attempts.append(Attempted(e["attempt"], intent_at=e["at"]))
            elif kind == "start":
                att = _attempt(st, e["attempt"])
                att.run_id = e["run_id"]
            elif kind == "end":
                att = _attempt(st, e["attempt"])
                att.run_id = att.run_id or e.get("run_id")
                att.outcome = e["outcome"]
                att.infrastructure_failure = e.get("infrastructure_failure")
        return states


def _attempt(st: ItemState, number: int) -> Attempted:
    for a in st.attempts:
        if a.number == number:
            return a
    raise ValueError(f"checkpoint: attempt {number} has no intent line")


# ── The driver ────────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Report:
    started: list[str] = field(default_factory=list)  # item keys started in THIS invocation
    skipped_done: list[str] = field(default_factory=list)
    reconciled: list[str] = field(default_factory=list)
    interrupted: list[str] = field(default_factory=list)
    reruns: list[str] = field(default_factory=list)


async def generate(
    items: Sequence[Item],
    runner: Runner,
    checkpoint: Checkpoint,
    wave: Wave,
    *,
    stop_after: int | None = None,
) -> Report:
    """Run (or resume) one wave. `stop_after` ends the invocation after that many NEW starts, for
    tests that simulate a batch that did not finish; the checkpoint is what carries on."""
    report = Report()
    if wave.startswith("proposed:") and (position := int(wave.split(":")[1])) > 1:
        # Refused, not warned: campaign N+1 reads the memory that N's approval writes.
        if not await runner.decided("proposed", position - 1):
            raise GenerationStopped(
                f"{wave}: refused. Every proposed campaign at position {position - 1} needs its "
                "human decision first, because memory is written on approval (Rules.md T6)."
            )
    started_now = 0
    for item in items:
        state = checkpoint.state().setdefault(item.key, ItemState())
        if state.done:
            report.skipped_done.append(item.key)
            continue
        open_attempt = state.open_attempt
        if open_attempt is not None:
            if await _reconcile(item, open_attempt, runner, checkpoint, report):
                continue
            state = checkpoint.state()[item.key]
        while True:
            action = next_action(state.counted()) if state.counted() else "rerun"
            if action == "accept":
                break
            if action == "escalate":
                raise GenerationStopped(
                    f"{item.key}: the original and {MAX_RERUNS} re-runs all failed on "
                    "infrastructure. It comes to the owner and is not re-run."
                )
            is_rerun = any(a.outcome == "invalidated" for a in state.attempts)
            if not is_rerun and stop_after is not None and started_now >= stop_after:
                return report  # counts ITEMS begun; a re-run of the item in hand is not a new item
            number = len(state.attempts) + 1
            if is_rerun:
                report.reruns.append(item.key)
            else:
                started_now += 1
            checkpoint.write({"event": "intent", "item": item.key, "attempt": number})
            run_id = await runner.start(item)
            checkpoint.write(
                {"event": "start", "item": item.key, "attempt": number, "run_id": run_id}
            )
            report.started.append(item.key)
            outcome, failure = await runner.wait(run_id)
            checkpoint.write(
                {
                    "event": "end",
                    "item": item.key,
                    "attempt": number,
                    "run_id": run_id,
                    "outcome": outcome,
                    "infrastructure_failure": failure,
                }
            )
            state = checkpoint.state()[item.key]
            if state.done:
                break
    return report


async def _reconcile(
    item: Item, open_attempt: Attempted, runner: Runner, checkpoint: Checkpoint, report: Report
) -> bool:
    """An attempt with no `end`: the process died while it was in flight. True if the item is now
    settled (its run reached an outcome, which is recorded and not repeated)."""
    run_id = open_attempt.run_id
    if run_id is None and open_attempt.intent_at is not None:
        since = datetime.fromisoformat(open_attempt.intent_at)
        run_id = await runner.find_orphan(item, since)
        if run_id is not None:
            checkpoint.write(
                {
                    "event": "start",
                    "item": item.key,
                    "attempt": open_attempt.number,
                    "run_id": run_id,
                }
            )
    if run_id is not None:
        outcome, failure = await runner.status(run_id)
        if outcome == "running":
            # Possibly still alive in the worker (only the harness died): let it finish rather
            # than start a second run beside it. `wait` has a timeout; a run whose worker is gone
            # comes back `running` and is recorded as interrupted below.
            outcome, failure = await runner.wait(run_id)
        if outcome != "running":
            checkpoint.write(
                {
                    "event": "end",
                    "item": item.key,
                    "attempt": open_attempt.number,
                    "run_id": run_id,
                    "outcome": outcome,
                    "infrastructure_failure": failure,
                    "reconciled": True,
                }
            )
            report.reconciled.append(item.key)
            return outcome in ("parked", "ended")
    checkpoint.write(
        {
            "event": "end",
            "item": item.key,
            "attempt": open_attempt.number,
            "run_id": run_id,
            "outcome": "interrupted",
            "infrastructure_failure": None,
        }
    )
    report.interrupted.append(item.key)
    return False
