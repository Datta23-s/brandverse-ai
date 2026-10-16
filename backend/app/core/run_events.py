"""Run-progress events, stored per run in a Redis Stream.

Why a stream rather than pub/sub: the worker writes each event once, and any number of readers —
a browser that connects late, one that reconnects after a dropped connection, a second tab —
replay from wherever they are. The stream entry ID is the SSE `id`, so a browser's automatic
reconnect (`Last-Event-ID`) resumes exactly after the last event it saw. Streams carry the TTL and
MAXLEN from `Settings` and persist through Redis' AOF.

Writers: the API (`run.queued`) and the worker (everything after). Reader: the SSE endpoint.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError

from app.errors import RunEventCorrupt
from app.models.runs import TERMINAL_EVENT_TYPES, RunEvent, run_event_adapter

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)

_FIELD = b"event"
# Read at most this many entries per round trip.
_READ_COUNT = 100
STREAM_START = "0-0"


def stream_key(run_id: UUID) -> str:
    return f"run:{run_id}:events"


def parse_entry_id(entry_id: str) -> tuple[int, int] | None:
    """`"1697040000000-3"` -> `(1697040000000, 3)`; None if not a stream entry ID."""
    ms, sep, seq = entry_id.partition("-")
    if not sep or not ms.isdigit() or not seq.isdigit():
        return None
    return int(ms), int(seq)


@dataclass(frozen=True, slots=True)
class StoredEvent:
    entry_id: str
    event: RunEvent

    @property
    def is_terminal(self) -> bool:
        return self.event.type in TERMINAL_EVENT_TYPES


class RunEventLog:
    def __init__(self, redis: "Redis", *, ttl_s: int, maxlen: int, block_ms: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._maxlen = maxlen
        self._block_ms = block_ms

    async def append(self, event: RunEvent) -> str:
        """Validate, append and refresh the stream's TTL in one transaction; return the entry ID."""
        key = stream_key(event.run_id)
        payload = run_event_adapter.dump_json(event)
        async with self._redis.pipeline(transaction=True) as tx:
            tx.xadd(key, {_FIELD: payload}, maxlen=self._maxlen, approximate=True)
            tx.expire(key, self._ttl_s)
            entry_id, _ = await tx.execute()
        return _text(entry_id)

    async def last(self, run_id: UUID) -> StoredEvent | None:
        """The most recent event, or None if the run has no stream (unknown or expired)."""
        entries = await self._redis.xrevrange(stream_key(run_id), count=1)
        if not entries:
            return None
        entry_id, fields = entries[0]
        return _decode(run_id, entry_id, fields)

    async def follow(self, run_id: UUID, after: str = STREAM_START) -> AsyncIterator[StoredEvent]:
        """Yield events after `after`, waiting for new ones, until the run's terminal event.

        Ends early if the stream disappears (expired) or if the terminal event was already
        behind `after` when reading started.
        """
        key = stream_key(run_id)
        cursor = after
        while True:
            response = await self._redis.xread(
                {key: cursor}, count=_READ_COUNT, block=self._block_ms
            )
            if not response:
                # Nothing new within the block window: is there anything left to wait for?
                last = await self.last(run_id)
                if last is None or (last.is_terminal and at_or_before(last.entry_id, cursor)):
                    return
                continue
            for entry_id, fields in response[0][1]:
                stored = _decode(run_id, entry_id, fields)
                cursor = stored.entry_id
                yield stored
                if stored.is_terminal:
                    return


def at_or_before(entry_id: str, cursor: str) -> bool:
    a, b = parse_entry_id(entry_id), parse_entry_id(cursor)
    return a is not None and b is not None and a <= b


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _decode(run_id: UUID, entry_id: bytes | str, fields: dict[bytes, bytes]) -> StoredEvent:
    entry = _text(entry_id)
    try:
        event = run_event_adapter.validate_json(fields[_FIELD])
    except (KeyError, ValidationError) as e:
        log.error("unreadable run event", extra={"stream_run_id": str(run_id), "entry_id": entry})
        raise RunEventCorrupt(f"run {run_id} entry {entry} is not a valid run event") from e
    return StoredEvent(entry_id=entry, event=event)
