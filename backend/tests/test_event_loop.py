"""psycopg's async mode needs the selector loop on Windows; this makes the rule a refusal, not a
reminder (owner, 2026-11-02, after a script came up on the Proactor loop despite Architecture.md
section 11 saying not to)."""

import asyncio
import re
import sys
from pathlib import Path

import pytest

from app.core.event_loop import WrongEventLoop, require_selector_loop

BACKEND = Path(__file__).resolve().parents[1]
# A CALL that starts the program's loop (at the start of a line, or inside sys.exit(...)), not a
# mention of it in a docstring or a comment.
ENTRY_POINT = re.compile(r"^[ \t]*(?:sys\.exit\()?asyncio\.run\(", re.MULTILINE)


def test_no_loop_running_is_not_an_error() -> None:
    require_selector_loop()  # a synchronous caller: nothing to check yet


def test_the_selector_loop_is_accepted() -> None:
    async def check() -> str:
        require_selector_loop()
        return "ok"

    assert asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop) == "ok"


@pytest.mark.skipif(sys.platform != "win32", reason="the Proactor loop exists on Windows only")
def test_the_proactor_loop_is_refused_with_the_cause_and_the_fix() -> None:
    async def check() -> None:
        require_selector_loop()

    with pytest.raises(WrongEventLoop, match="SelectorEventLoop"):
        asyncio.run(check(), loop_factory=asyncio.ProactorEventLoop)


@pytest.mark.skipif(sys.platform != "win32", reason="the Proactor loop exists on Windows only")
def test_creating_a_database_pool_on_the_wrong_loop_fails_at_once_not_after_a_timeout() -> None:
    from app.core.db import create_db_pool
    from tests.conftest import make_settings

    settings = make_settings(database_url="postgresql://u:p@127.0.0.1:5432/db")

    async def make() -> None:
        create_db_pool(settings, "api")

    with pytest.raises(WrongEventLoop):
        asyncio.run(make(), loop_factory=asyncio.ProactorEventLoop)


def test_every_entry_point_in_the_application_and_its_scripts_names_the_selector_loop() -> None:
    """A new `asyncio.run(` with the default loop cannot be added: this fails, naming the file."""
    offenders = []
    for path in [*(BACKEND / "app").rglob("*.py"), *(BACKEND / "scripts").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        for match in ENTRY_POINT.finditer(text):
            call = text[match.start() : text.find("\n", match.start())]
            if "loop_factory=asyncio.SelectorEventLoop" not in call:
                offenders.append(f"{path.relative_to(BACKEND)}: {call.strip()}")
    assert not offenders, "asyncio.run without the selector loop:\n" + "\n".join(offenders)
