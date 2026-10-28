"""The steering SET of the rate probes: only an engine inside it can break a hold."""

import asyncio
from typing import Any

from scripts.rate_probe_lib import observe

SEVEN = ("yep", "yandex", "yahoo", "privacywall", "duckduckgo web", "swisscows", "mojeek")


class FakeSearch:
    """Stands in for SearxngSearch: fills `last` the way the probe's response hook does."""

    def __init__(self, last: dict[str, Any], body: dict[str, Any]) -> None:
        self.last, self.body = last, body

    async def search(self, query: str, limit: int = 10) -> Any:
        self.last["status"], self.last["body"] = 200, self.body

        class R:
            results: list[Any] = []

        return R()


def run(body: dict[str, Any], engine: str | tuple[str, ...]) -> Any:
    last: dict[str, Any] = {}
    return asyncio.run(
        observe(FakeSearch(last, body), last, engine=engine, n=0, group="general", query="q")  # type: ignore[arg-type]
    )


def rows(*names: str) -> list[dict[str, Any]]:
    return [{"url": f"https://{n}.example/", "engines": [n]} for n in names]


def test_brave_refused_does_not_break_a_hold_steered_by_the_seven():
    body = {"results": rows(*SEVEN), "unresponsive_engines": [["brave", "too many requests"]]}
    assert run(body, SEVEN).clean is True
    assert run(body, "brave").clean is False  # the old single-engine meaning is unchanged


def test_any_one_of_the_seven_failing_breaks_it_and_is_named():
    body = {"results": rows("yep"), "unresponsive_engines": [["mojeek", "CAPTCHA"]]}
    obs = run(body, SEVEN)
    assert obs.clean is False
    assert obs.record["steering_failed"] == {"mojeek": "CAPTCHA"}
    assert obs.record["steering_rows"]["yep"] == 1
