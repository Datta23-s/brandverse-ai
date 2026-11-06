"""Memory injection (decision-log 2.36), answered WITHOUT a provider.

Whether a recalled item reaches the copy writer's prompt, in which band, and what the run
records. Phases.md exit criterion: a
recalled item at M >= 0.90 reaches the agent verbatim, one at 0.80-0.89 is rephrasable, one at
0.65-0.79 is context only, one below 0.65 does not reach it."""

from typing import Any

import pytest

from app.agents.copywriter import CopyWriter
from app.memory.injection import (
    InjectionPolicy,
    clean,
    memory_block,
    select_injection,
)
from app.memory.scoring import load_scoring
from tests.llm_fakes import prompt_library
from tests.test_copywriter import FakeCompletion, agent_ctx, draft, gateway, sent
from tests.test_orchestrator_graph import VERIFIED, Harness, truth_with

pytestmark = pytest.mark.anyio

SCORING = load_scoring()
POLICY = InjectionPolicy(SCORING.memory.bands, SCORING.necessity.item_cap)


def hit(i: int, m: float, kind: str = "insight", text: str | None = None) -> dict[str, Any]:
    return {"id": f"m{i}", "kind": kind, "content": text or f"note number {i}", "m": m, "band": "x"}


def test_the_registered_bands_and_cap_are_the_ones_used() -> None:
    assert (POLICY.bands.reuse, POLICY.bands.adapt, POLICY.bands.support) == (0.90, 0.80, 0.65)
    assert POLICY.cap == 12


@pytest.mark.parametrize(
    ("m", "band", "label"),
    [
        (0.97, "reuse", "use as written"),
        (0.90, "reuse", "use as written"),  # the boundary belongs to the higher band
        (0.89, "adapt", "adapt"),
        (0.80, "adapt", "adapt"),
        (0.79, "support", "background"),
        (0.65, "support", "background"),
    ],
)
def test_an_item_is_injected_in_the_band_its_score_puts_it_in(
    m: float, band: str, label: str
) -> None:
    inj = select_injection([hit(1, m, text="Category leaders lead with price")], POLICY)
    assert [(i.band, i.text) for i in inj.items] == [(band, "Category leaders lead with price")]
    assert memory_block(inj.record()) == f"- [{label}] Category leaders lead with price"


@pytest.mark.parametrize("m", [0.649, 0.5, 0.0])
def test_an_item_below_the_floor_is_not_injected_and_is_counted_as_a_gap(m: float) -> None:
    inj = select_injection([hit(1, m)], POLICY)
    assert inj.items == () and inj.below_floor == 1 and inj.considered == 1
    assert memory_block(inj.record()) == "(none)"


def test_only_the_two_memory_kinds_are_injected() -> None:
    inj = select_injection(
        [hit(1, 0.95, "claim"), hit(2, 0.95, "persona"), hit(3, 0.95, "competitor")], POLICY
    )
    assert [i.id for i in inj.items] == ["m3"]


def test_at_most_twelve_are_injected_best_first_and_the_excess_is_counted() -> None:
    hits = [hit(i, 0.66 + i * 0.01) for i in range(20)]  # m 0.66 .. 0.85
    inj = select_injection(hits, POLICY)
    assert len(inj.items) == 12 == inj.cap and inj.over_cap == 8
    assert [i.id for i in inj.items] == [f"m{i}" for i in range(19, 7, -1)]
    assert [i.m for i in inj.items] == sorted((i.m for i in inj.items), reverse=True)


def test_ties_are_broken_by_id_so_the_choice_is_deterministic() -> None:
    a = select_injection([hit(2, 0.9), hit(1, 0.9), hit(3, 0.9)], POLICY)
    b = select_injection([hit(3, 0.9), hit(2, 0.9), hit(1, 0.9)], POLICY)
    assert [i.id for i in a.items] == [i.id for i in b.items] == ["m1", "m2", "m3"]


def test_the_record_carries_what_the_run_reused_and_where() -> None:
    rec = select_injection([hit(1, 0.92), hit(2, 0.7), hit(3, 0.2)], POLICY).record()
    assert rec["agents"] == ["A6"] and rec["cap"] == 12
    assert (rec["considered"], rec["below_floor"], rec["over_cap"]) == (3, 1, 0)
    assert [(i["id"], i["band"]) for i in rec["items"]] == [("m1", "reuse"), ("m2", "support")]


def test_a_note_cannot_close_or_open_a_wrapper_tag_or_run_on() -> None:
    assert clean("hello </page> <note>x") == "hello [note> [note>x"
    assert len(clean("x" * 500)) == 200 and "\n" not in clean("a\n\nb")


# ── The prompt: a recalled item changes the agent's input, per band ───────────────────────────


async def prompt_with(items: list[dict[str, Any]]) -> str:
    completion = FakeCompletion(draft())
    gw, _ = gateway(completion)
    state = {"injection": select_injection(items, POLICY).record(), "brief": "Launch"}
    await CopyWriter(gw, prompt_library()).run(state, agent_ctx(truth_with(VERIFIED)))
    return sent(completion)


async def test_a_recalled_item_changes_the_copy_writers_input_in_each_band() -> None:
    none = await prompt_with([])
    assert "Remembered notes from earlier campaigns (context only):\n(none)" in none
    seen = {}
    for m, label in ((0.95, "use as written"), (0.85, "adapt"), (0.70, "background")):
        p = await prompt_with([hit(1, m, text=f"unique-note-{label}")])
        assert p != none and f"- [{label}] unique-note-{label}" in p
        seen[label] = p
    assert len(set(seen.values())) == 3  # three bands, three different inputs
    below = await prompt_with([hit(1, 0.40, text="should-not-appear")])
    assert below == none and "should-not-appear" not in below


async def test_the_prompt_defines_each_label_and_says_notes_are_not_orders() -> None:
    p = await prompt_with([hit(1, 0.95)])
    assert "[use as written]" in p and "[adapt]" in p and "[background]" in p
    assert "not instructions" in p and "never this product" in p


async def test_an_arm_with_no_injection_uses_the_same_prompt_version_with_none() -> None:
    completion = FakeCompletion(draft())
    gw, ledger = gateway(completion)
    await CopyWriter(gw, prompt_library()).run({}, agent_ctx(truth_with(VERIFIED)))
    (row,) = ledger.rows.values()
    assert (row["prompt"].prompt_id, row["prompt"].prompt_version) == ("a6_copy", "v3")
    assert "(none)" in sent(completion)


# ── The graph: step 9 injects from the step-8 recall and records it with the strategy ──────────


async def test_the_pipeline_injects_from_the_rescored_recall_and_keeps_the_record() -> None:
    from app.orchestrator.deps import MemoryResult

    h = Harness()
    h.deps.injection = POLICY
    recalled = [hit(1, 0.93), hit(2, 0.82), hit(3, 0.70), hit(4, 0.3)]

    async def retrieve(state: Any, personas: Any = None) -> MemoryResult:
        return MemoryResult(recalled, 0.5)

    h.deps.retrieve = retrieve
    out = await h.start()
    inj = out.state["injection"]
    assert [(i["id"], i["band"]) for i in inj["items"]] == [
        ("m1", "reuse"),
        ("m2", "adapt"),
        ("m3", "support"),
    ]
    assert out.state["strategy"]["injection"] == inj and inj["below_floor"] == 1


async def test_without_a_policy_nothing_is_injected() -> None:
    out = await Harness().start()
    assert out.state["injection"] is None
