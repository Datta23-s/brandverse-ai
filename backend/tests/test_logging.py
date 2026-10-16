"""Structured logging: JSON lines, correlated by run_id (Rules.md P12)."""

import asyncio
import io
import json
import logging
from collections.abc import Iterator

import pytest

from app.core.logging import bind_run_id, configure_logging, current_run_id


class LogLines:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    def read(self) -> list[dict[str, object]]:
        """This test module's lines only (asyncio logs its own at DEBUG)."""
        out = self.stream.getvalue()
        self.stream.seek(0)
        self.stream.truncate()
        entries = [json.loads(line) for line in out.splitlines()]
        return [e for e in entries if e["logger"] == "t"]


@pytest.fixture
def lines() -> Iterator[LogLines]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    captured = LogLines()
    configure_logging("DEBUG", stream=captured.stream)
    yield captured
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_one_json_object_per_line_with_extras(lines: LogLines) -> None:
    logging.getLogger("t").info("hello %s", "world", extra={"step": 3})
    (entry,) = lines.read()
    assert entry["msg"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "t"
    assert entry["step"] == 3
    assert entry["run_id"] is None
    assert str(entry["ts"]).endswith("+00:00")


def test_run_id_bound_then_released(lines: LogLines) -> None:
    log = logging.getLogger("t")
    with bind_run_id("run-1"):
        log.info("inside")
        assert current_run_id() == "run-1"
    log.info("outside")
    inside, outside = lines.read()
    assert inside["run_id"] == "run-1"
    assert outside["run_id"] is None


@pytest.mark.anyio
async def test_run_id_follows_into_tasks_started_inside(lines: LogLines) -> None:
    async def child() -> None:
        logging.getLogger("t").info("child")

    with bind_run_id("run-2"):
        await asyncio.create_task(child())
    (entry,) = lines.read()
    assert entry["run_id"] == "run-2"


def test_exceptions_are_logged_with_traceback(lines: LogLines) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("t").exception("failed")
    (entry,) = lines.read()
    assert "ValueError: boom" in str(entry["exc"])


def test_reconfiguring_does_not_duplicate_output(lines: LogLines) -> None:
    configure_logging("INFO", stream=lines.stream)
    logging.getLogger("t").info("once")
    assert len(lines.read()) == 1


def test_non_ascii_survives_a_cp1252_stream() -> None:
    # Windows redirects stdout as cp1252; ARQ logs "→" and "●". Nothing may be lost.
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("INFO", stream=stream)
        logging.getLogger("t").info("job ● 0.5s → done")
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    (line,) = raw.getvalue().decode("ascii").splitlines()
    assert json.loads(line)["msg"] == "job ● 0.5s → done"
