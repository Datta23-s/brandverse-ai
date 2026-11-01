"""Where a run's local time goes (app/core/timing.py; migration 0044)."""

import asyncio

import pytest

from app.core.timing import TimedEncoder, TimedSearch, add, collecting, merge_timings

pytestmark = pytest.mark.anyio


class SlowEncoder:
    model = "m"
    version = "v"

    def embed_queries(self, texts: list[str]) -> list[int]:
        return [len(t) for t in texts]

    def embed_documents(self, texts: list[str]) -> list[int]:
        import time

        time.sleep(0.02)
        return [len(t) for t in texts]


class FakeSearch:
    async def search(self, query: str, *, limit: int = 10) -> str:
        await asyncio.sleep(0.02)
        return query


def test_a_timed_encoder_records_under_its_name_and_delegates_the_rest() -> None:
    enc = TimedEncoder(SlowEncoder(), "encoder_recall_ms")
    with collecting() as t:
        assert enc.embed_documents(["ab", "c"]) == [2, 1]
        assert enc.embed_queries(["xyz"]) == [3]
    assert enc.model == "m" and enc.version == "v"  # not a timed method: passed through
    assert set(t) == {"encoder_recall_ms"} and t["encoder_recall_ms"] >= 10


async def test_a_timed_search_records_search_ms() -> None:
    with collecting() as t:
        assert await TimedSearch(FakeSearch()).search("q") == "q"  # type: ignore[arg-type]
    assert t["search_ms"] >= 10  # a 20 ms sleep can return a little early on a coarse timer


async def test_two_runs_at_once_do_not_write_into_each_others_figures() -> None:
    async def run(name: str, ms: float) -> dict[str, float]:
        with collecting() as t:
            await asyncio.sleep(0.01)
            add(name, ms)
            await asyncio.sleep(0.01)
            add(name, ms)
        return t

    a, b = await asyncio.gather(run("a", 1.0), run("b", 5.0))
    assert a == {"a": 2.0} and b == {"b": 10.0}


def test_outside_a_collecting_block_nothing_is_recorded_and_nothing_fails() -> None:
    add("x", 1.0)
    assert TimedEncoder(SlowEncoder(), "n").embed_queries(["a"]) == [1]


def test_the_state_reducer_adds_figures_of_the_same_name() -> None:
    assert merge_timings({"a": 1.0}, {"a": 2.0, "b": 3.0}) == {"a": 3.0, "b": 3.0}
    assert merge_timings(None, None) == {}


@pytest.mark.parametrize("name", ["search_ms", "encoder_recall_ms"])
def test_a_name_is_just_a_key(name: str) -> None:
    with collecting() as t:
        add(name, 4.0)
    assert t == {name: 4.0}
