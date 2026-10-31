"""The temperature is part of the LOCKED CALL CONFIGURATION, not an afterthought to the prompt text.

On 2026-10-30 the same attribute on the same copy was judged differently by identical classifier
calls (V4, V12), because no temperature was set and the provider's default applies. The classifier
and A7's risk read are judgement tasks, where variance is noise and not creativity, so they run at
temperature 0; A6 and the other generating agents keep the default.

A hash-locked prompt called at another temperature would be a different thing and "the prompt is
locked" would mislead. So the value lives IN the prompt file (`temperature:`), under the same
hash: changing it means a new version. `llm_calls` already records prompt id and version, so a row
names the temperature it was made at through the version it names.

What these tests do not show: that temperature 0 makes the provider's answer identical across
calls (it lowers the variance; whether a given model is exactly repeatable is a live
observation)."""

from pathlib import Path

import pytest

from app.agents.risk import A7_RISK
from app.core.claim_classifier import CLAIM_EXTRACT
from app.llm.prompts import file_sha256, load_library
from tests.llm_fakes import FakeCompletion, prompt_library, reply
from tests.test_copywriter import draft, gateway

pytestmark = pytest.mark.anyio

PROMPTS = Path(__file__).resolve().parents[2] / "config" / "prompts"
JUDGEMENT = {"claim_extract", "a7_risk"}


def test_the_judgement_prompts_in_use_are_at_temperature_zero() -> None:
    library = prompt_library()
    assert library.get(*CLAIM_EXTRACT).temperature == 0
    assert library.get(*A7_RISK).temperature == 0
    assert CLAIM_EXTRACT[1] == "v3" and A7_RISK[1] == "v2"


def test_the_earlier_versions_are_untouched_and_set_none() -> None:
    library = prompt_library()
    for ref in (("claim_extract", "v1"), ("claim_extract", "v2"), ("a7_risk", "v1")):
        assert library.get(*ref).temperature is None  # released, locked, called at the default


def test_generation_keeps_its_setting() -> None:
    library = prompt_library()
    for ref in library.refs():
        if ref.prompt_id not in JUDGEMENT and ref.prompt_id in {
            "a6_copy",
            "a1_positioning",
            "a2_market",
            "a3_personas",
        }:
            assert library.get(ref.prompt_id, ref.prompt_version).temperature is None


def test_the_temperature_is_under_the_hash_so_changing_it_is_a_new_version(tmp_path: Path) -> None:
    source = PROMPTS / "claim_extract" / "v3.yaml"
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "\ntemperature: 0\n" in text
    other = tmp_path / "v3.yaml"
    other.write_text(text.replace("\ntemperature: 0\n", "\ntemperature: 1\n"), encoding="utf-8")
    assert file_sha256(other) != file_sha256(source)
    # and the library refuses the edited file against the lock (copy the tree, edit one file)
    tree = tmp_path / "prompts"
    for p in PROMPTS.glob("*/*.yaml"):
        (tree / p.parent.name).mkdir(parents=True, exist_ok=True)
        (tree / p.parent.name / p.name).write_bytes(p.read_bytes())
    (tree / "versions.lock").write_bytes((PROMPTS / "versions.lock").read_bytes())
    (tree / "claim_extract" / "v3.yaml").write_text(
        text.replace("\ntemperature: 0\n", "\ntemperature: 1\n"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="differs from the version recorded"):
        load_library(tree)


def test_v3_differs_from_v2_by_the_temperature_and_its_comment_only() -> None:
    def body(version: str) -> list[str]:
        lines = (PROMPTS / "claim_extract" / f"{version}.yaml").read_text(encoding="utf-8")
        return [ln for ln in lines.replace("\r\n", "\n").split("\n") if not ln.startswith("#")]

    v2 = [ln for ln in body("v2") if not ln.startswith("version:")]
    v3 = [ln for ln in body("v3") if not ln.startswith(("version:", "temperature:"))]
    assert v2 == v3  # the same instructions, the same schema, the same user template


async def test_the_gateway_calls_the_provider_at_the_prompts_temperature() -> None:
    completion = FakeCompletion(reply('{"statements": []}'))
    gw, ledger = gateway(completion)
    from app.core.claim_classifier import ExtractedStatements
    from tests.llm_fakes import ctx

    prompt = prompt_library().get(*CLAIM_EXTRACT).render(copy="x", claims="(none)")
    assert prompt.temperature == 0
    await gw.structured(
        ExtractedStatements,
        tier=__import__("app.llm.registry", fromlist=["Tier"]).Tier.STANDARD,
        prompt=prompt,
        ctx=ctx(),
        route_reason="test",
    )
    assert completion.calls[0]["temperature"] == 0


async def test_a_prompt_with_no_temperature_sends_none_so_the_default_applies() -> None:
    from app.agents.copywriter import CopyWriter
    from tests.test_copywriter import V1, agent_ctx
    from tests.test_orchestrator_graph import truth_with

    completion = FakeCompletion(draft())
    gw, _ = gateway(completion)
    await CopyWriter(gw, prompt_library()).run({}, agent_ctx(truth_with(V1)))
    assert "temperature" not in completion.calls[0]


async def test_the_repair_of_a_temperature_zero_call_is_at_temperature_zero() -> None:
    completion = FakeCompletion(reply("not json"), reply('{"statements": []}'))
    gw, ledger = gateway(completion)
    from app.core.claim_classifier import ExtractedStatements
    from app.llm.registry import Tier
    from tests.llm_fakes import ctx

    prompt = prompt_library().get(*CLAIM_EXTRACT).render(copy="x", claims="(none)")
    await gw.structured(
        ExtractedStatements, tier=Tier.STANDARD, prompt=prompt, ctx=ctx(), route_reason="test"
    )
    assert [c.get("temperature") for c in completion.calls] == [0, 0]
    assert {r["prompt"].prompt_id for r in ledger.rows.values()} == {
        "claim_extract",
        "structured_repair",
    }
