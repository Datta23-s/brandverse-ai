"""A file-backed fake of the generation run's I/O, shared by the in-process tests and the subprocess
that the kill test terminates. The "database" is a JSON file, so it survives the process dying, as
the real runs table does."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.generation import Checkpoint, Item, Outcome, generate, wave_items
from app.evaluation.schedule import registered_order, test_set_requests


class FileRunner:
    def __init__(
        self,
        db: Path,
        *,
        hang_on: str | None = None,
        outcomes: dict[str, list[tuple[Outcome, str | None]]] | None = None,
        die_after_start: str | None = None,
        decided_positions: frozenset[tuple[str, int]] = frozenset(),
        stuck: frozenset[str] = frozenset(),
    ) -> None:
        self.stuck = stuck
        self.db, self.hang_on, self.outcomes = db, hang_on, outcomes or {}
        self.die_after_start, self.decided_positions = die_after_start, decided_positions
        self.started: list[str] = []

    def _read(self) -> dict[str, dict[str, str | None]]:
        return json.loads(self.db.read_text(encoding="utf-8")) if self.db.exists() else {}

    def _write(self, data: dict[str, dict[str, str | None]]) -> None:
        self.db.write_text(json.dumps(data), encoding="utf-8")

    async def start(self, item: Item) -> str:
        data = self._read()
        run_id = f"run-{len(data) + 1}"
        data[run_id] = {
            "item": item.key,
            "status": "running",
            "failure": None,
            "at": datetime.now(UTC).isoformat(),
        }
        self._write(data)
        self.started.append(item.key)
        if item.key == self.die_after_start:
            raise SystemExit("killed between the run starting and the checkpoint hearing of it")
        return run_id

    async def find_orphan(self, item: Item, since: datetime) -> str | None:
        for run_id, row in self._read().items():
            if row["item"] == item.key and datetime.fromisoformat(str(row["at"])) >= since:
                return run_id
        return None

    async def wait(self, run_id: str) -> tuple[Outcome, str | None]:
        row = self._read()[run_id]
        if row["status"] != "running":
            return row["status"], row["failure"]  # type: ignore[return-value]
        if row["item"] == self.hang_on:
            await asyncio.sleep(3600)
        if run_id in self.stuck:  # the worker is gone: `wait` times out with the run still running
            return "running", None
        script = self.outcomes.get(str(row["item"]))
        outcome, failure = script.pop(0) if script else ("parked", None)
        data = self._read()
        data[run_id].update(status=outcome, failure=failure)
        self._write(data)
        return outcome, failure

    async def status(self, run_id: str) -> tuple[Outcome, str | None]:
        row = self._read()[run_id]
        return row["status"], row["failure"]  # type: ignore[return-value]

    async def decided(self, arm: str, position: int) -> bool:
        return (arm, position) in self.decided_positions


def round_one_items() -> list[Item]:
    """Round 1: all three arms of the ten position-1 requests, in the registered order."""
    positions = {r["id"]: r["position"] for r in test_set_requests()}
    return wave_items(registered_order(), positions, "round:1")


async def _main(directory: str, hang_on: str) -> None:
    d = Path(directory)
    await generate(
        round_one_items(),
        FileRunner(d / "db.json", hang_on=hang_on),
        Checkpoint(d / "checkpoint.jsonl"),
        "round:1",
    )


if __name__ == "__main__":  # the process the kill test terminates
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
