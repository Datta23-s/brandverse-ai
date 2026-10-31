"""Threat V11, the audience in production research, and the runs columns that exist only at the end.

V11 (docs/decision-log.md): on 2026-10-30 the first full run on real models wrote the headline "Make
your commute easier with a lightweight walking shoe" for request R10. "Lightweight" came from the
brief, no claim backed it, and the classifier extracted no statement for it because it sat inside a
noun phrase, so NO gate judged it. This is not V4 (a statement citing a claim that does not entail
it): here the statement was never extracted. `claim_extract@v2` asks the model to extract property
assertions embedded in noun phrases. These tests pin what the code does with that, offline:

- the exact copy from the run, with only the statements v1 extracted, PASSES (the hole, reproduced);
- the same copy with the statement v2 is asked to add is BLOCKED by the existing gate;
- v2 is what the classifier calls, v1 is still in the library and unchanged.

They do NOT show that the real model extracts "lightweight" under v2: that is a live observation
(Phases.md, 2026-10-30), and its recall on other attributes is unmeasured. There is deliberately no
word list in the code: a deterministic adjective lexicon would be a brittle detector (T5).
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import AgentContext
from app.core.claim_classifier import CLAIM_EXTRACT
from app.core.sector_registry import Sector
from app.core.truth import Claim, ProductTruth
from app.evaluation.test_set import TEST_SET, plan_requests
from app.orchestrator.adapters import plan_request_from_state
from app.research.planner import plan_queries
from tests.llm_fakes import prompt_library
from tests.test_claim_classifier import check, model_says

pytestmark = pytest.mark.anyio

SPEC = "https://neemans.com/products/begin-walk-lite-for-men-grey"
R10_TRUTH = ProductTruth(
    Sector("footwear", regulated=False),
    (
        Claim("c1", "Knit upper", "verified", SPEC, False),
        Claim("c2", "Rubber and EVA sole", "verified", SPEC, False),
        Claim(
            "c3",
            "Lace-up, low top, medium width, almond toe, low arch",
            "verified",
            SPEC,
            False,
        ),
        Claim("c4", "Not water resistant", "verified", SPEC, False),
    ),
    (),
)
# The copy A6 wrote in the 2026-10-30 run, exactly.
R10_COPY = {
    "headline": "Make your commute easier with a lightweight walking shoe",
    "body": (
        "Transition smoothly as you walk to and from transit with a footwear choice designed for "
        "your journey. This design features a knit upper along with a rubber and EVA sole. "
        "You will find a lace-up, low top, medium width, almond toe, and low arch structure. "
        "Please note that this product is not water resistant."
    ),
    "call_to_action": "Discover more today",
}
WHAT_V1_EXTRACTED = (
    ("This design features a knit upper along with a rubber and EVA sole.", "c1"),
    ("This design features a knit upper along with a rubber and EVA sole.", "c2"),
    ("You will find a lace-up, low top, medium width, almond toe, and low arch structure.", "c3"),
    ("Please note that this product is not water resistant.", "c4"),
)


async def test_the_hole_the_run_exposed_is_reproduced_with_what_v1_extracted() -> None:
    report, _, _ = await check(R10_COPY, R10_TRUTH, model_says(*WHAT_V1_EXTRACTED), strong=False)
    assert report.outcome == "pass" and report.findings == ()  # "lightweight" judged by no gate


async def test_a_property_statement_v2_is_asked_to_add_is_blocked_for_having_no_claim() -> None:
    said = (*WHAT_V1_EXTRACTED, ("lightweight walking shoe", None))
    report, _, _ = await check(R10_COPY, R10_TRUTH, model_says(*said), strong=False)
    assert report.outcome == "block"
    (finding,) = report.findings
    assert finding.kind == "unsourced_claim" and "lightweight walking shoe" in finding.message


async def test_a_property_cited_to_an_unrelated_claim_is_no_longer_a_pass() -> None:
    """Before V12 the gate trusted the model's citation: "lightweight walking shoe" cited to the
    knit upper claim PASSED (it did, live, on 2026-10-30). The lexical citation check blocks it
    (tests/test_citation_terms.py has the exact draft)."""
    said = (*WHAT_V1_EXTRACTED, ("lightweight walking shoe", "c1"))
    report, _, _ = await check(R10_COPY, R10_TRUTH, model_says(*said), strong=False)
    assert report.outcome == "block"
    assert [f.kind for f in report.findings] == ["citation_lacks_term"]


def test_v2_is_the_classifier_prompt_and_asks_for_properties_in_noun_phrases() -> None:
    assert CLAIM_EXTRACT == ("claim_extract", "v2")
    library = prompt_library()
    v2 = library.get(*CLAIM_EXTRACT)
    system = v2.system or ""
    assert "does not have to be a whole sentence" in system and "noun phrase" in system
    assert "lightweight" in system  # the instruction's own example, not a detector
    v1 = library.get("claim_extract", "v1")
    assert "noun phrase" not in (v1.system or "")  # a released version is never edited


def test_the_code_holds_no_word_list_for_attributes() -> None:
    """No string in the code outside a docstring or a comment names the attribute: a lexicon of
    adjectives would be a brittle detector (T5). Docstrings may cite it as the example."""
    import ast

    offenders = []
    for p in (Path(__file__).resolve().parents[1] / "app").rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and id(n) not in docs
                and re.search(r"\blightweight\b", n.value)
            ):
                offenders.append(f"{p.name}: {n.value[:40]!r}")
    assert offenders == []


# ── Production research plans from the request's audience ────────────────────────────────────


def _state_for(request_id: str) -> dict[str, object]:
    import yaml

    data = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))
    request = next(r for r in data["requests"] if r["id"] == request_id)
    brand = next(b for b in data["brands"] if b["id"] == request["brand"])
    product = brand["products"][request["product"]]
    return {
        "product": {"category": product["category"]},
        "truth": {"sector": brand["sector"], "regulated": brand["regulated"]},
        "audience": request.get("audience_hint"),
    }


@pytest.mark.parametrize("request_id", [f"R{i:02d}" for i in range(1, 31)])
def test_production_plans_the_same_queries_the_evaluation_recorded(request_id: str) -> None:
    from app.research.planner import PlanRequest  # noqa: F401

    state = _state_for(request_id)
    produced = plan_queries(
        plan_request_from_state(state),  # type: ignore[arg-type]
        queries_per_facet=2,
        locale="India",
        max_pages_per_query=100,
    )
    recorded = plan_queries(
        plan_requests()[request_id], queries_per_facet=2, locale="India", max_pages_per_query=100
    )
    assert (
        produced.queries == recorded.queries
    )  # the objective is carried by the evaluation, unused


def test_without_a_stated_audience_the_plan_differs() -> None:
    """The finding: the first run planned without one and asked queries the snapshot never held."""
    state = _state_for("R10")
    with_audience = plan_queries(
        plan_request_from_state(state),  # type: ignore[arg-type]
        queries_per_facet=2,
        locale="India",
        max_pages_per_query=100,
    )
    state["audience"] = None
    without = plan_queries(
        plan_request_from_state(state),  # type: ignore[arg-type]
        queries_per_facet=2,
        locale="India",
        max_pages_per_query=100,
    )
    assert with_audience.queries != without.queries


# ── runs.llm_call_count and total_cost_usd exist only once a run has ended ───────────────────


def test_nothing_reads_the_runs_spend_columns_but_the_run_that_writes_them() -> None:
    """They are written by `end_run` from the ledger and are 0 while a run is parked at approval, so
    a reader of a run in flight must query `llm_calls` (Rules.md A1). Nothing in app/ reads them
    today; the approval view (chunk E) and the evaluation must keep it that way or name themselves
    here, with the reason their run has ended."""
    app = Path(__file__).resolve().parents[1] / "app"
    readers = {
        p.relative_to(app).as_posix()
        for p in app.rglob("*.py")
        if re.search(r"\b(llm_call_count|total_cost_usd)\b", p.read_text(encoding="utf-8"))
    }
    allowed = {
        "core/budgeted_run.py",  # writes them at end_run
        "evaluation/llm_attempts.py",  # documents them as spend and reads the ledger
    }
    assert readers <= allowed, f"read the end-only run columns: {sorted(readers - allowed)}"


# ── The forced revision, offline: the draft V11 let through is now revised ───────────────────


async def test_the_draft_with_lightweight_is_blocked_then_revised_and_the_second_draft_passes() -> (
    None
):
    from app.agents.copywriter import CopyWriter
    from app.agents.mock import mock_agents
    from app.agents.outputs import CreativeOutput
    from app.orchestrator.adapters import classifier_extractor
    from tests.llm_fakes import FakeCompletion
    from tests.test_copywriter import draft, gateway, sent
    from tests.test_orchestrator_graph import Harness

    class FirstThenReal:
        id = "A6"

        def __init__(self, first: CreativeOutput, real: CopyWriter) -> None:
            self.first, self.real = first, real

        async def run(self, state: Mapping[str, Any], ctx: AgentContext) -> CreativeOutput:
            if state.get("creative") is None:
                return self.first
            return await self.real.run(state, ctx)

    completion = FakeCompletion(
        # the classifier on draft 1: what v2 is asked to do, "lightweight" with no claim
        model_says(*WHAT_V1_EXTRACTED, ("lightweight walking shoe", None)),
        # A6's revision
        draft(
            headline="Easier commutes start here",
            body="A knit upper and a rubber and EVA sole. Not water resistant.",
        ),
        # the classifier on draft 2
        model_says(
            ("A knit upper and a rubber and EVA sole.", "c1"),
            ("A knit upper and a rubber and EVA sole.", "c2"),
            ("Not water resistant.", "c4"),
        ),
    )
    gw, ledger = gateway(completion)
    prompts = prompt_library()
    agents = {
        **mock_agents(),
        "A6": FirstThenReal(CreativeOutput(copy=R10_COPY), CopyWriter(gw, prompts)),
    }
    out = await Harness(
        truth=R10_TRUTH, agents=agents, extractor=classifier_extractor(gw, prompts)
    ).start()
    assert out.state["revision_round"] == 1
    assert (
        out.state["validation"]["outcome"] == "pass" and out.state["validation"]["findings"] == []
    )
    revision_prompt = sent(completion, 1)
    assert "not backed by any listed claim" in revision_prompt  # told the KIND of problem
    # the reviser is told WHERE (the span, for unsourced_claim only), never the message or the rest
    assert '"lightweight walking shoe"' in revision_prompt
    assert "cites no claim" not in revision_prompt and "Transition smoothly" not in revision_prompt
    assert [(c.agent_id, c.revision_round) for c in ledger.contexts][:3] == [
        ("A7", 0),
        ("A6", 1),
        ("A7", 1),
    ]
