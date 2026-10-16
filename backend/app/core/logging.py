"""Structured JSON logging correlated by `run_id` (Rules.md P12, Architecture.md §12).

One JSON object per line on stdout. `run_id` is carried in a context variable, so every log line
emitted while a run is bound — in the API, the worker, or any library they call — carries it
without being threaded through call signatures. Extra fields go in `extra={...}`.
"""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime", "run_id"}
)


def current_run_id() -> str | None:
    return _run_id.get()


@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    """Attach `run_id` to every log line emitted inside this block (and tasks it starts)."""
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        # ASCII-only: a Windows console or redirected stdout may be cp1252, and ARQ logs "→".
        return json.dumps(entry, default=str, ensure_ascii=True)


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Route all logging (ours, uvicorn's, ARQ's) through one JSON handler on stdout.

    Idempotent: replaces handlers on the root logger rather than adding to them.
    """
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_RunIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn and ARQ attach their own handlers in some modes; make them propagate to ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq"):
        lib = logging.getLogger(name)
        lib.handlers.clear()
        lib.propagate = True
