"""config/models.yaml is the only source of model IDs, and switching a tier's model is one line."""

import difflib
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import REPO_ROOT, get_settings
from app.llm.gateway import LLMGateway
from app.llm.registry import Tier, load_registry
from app.memory.embeddings import load_embedding_config
from tests.llm_fakes import (
    FakeCompletion,
    FakeLedger,
    ctx,
    judge,
    make_registry,
    prompt_library,
    reply,
)

MODELS_YAML = get_settings().models_config_path


def test_the_real_registry_loads_three_routable_tiers() -> None:
    registry = load_registry(MODELS_YAML)
    assert set(registry.routable) == {Tier.CHEAP, Tier.STANDARD, Tier.STRONG}
    for tier in Tier:
        model = registry.model(tier)
        assert model.litellm_model == f"{model.provider}/{model.id}"
        assert model.rate_limits.rpd > 0


def test_strong_tier_price_change_applies_from_its_date() -> None:
    strong = load_registry(MODELS_YAML).model(Tier.STRONG)
    assert strong.price_change is not None
    before = strong.cost_usd(1_000_000, 0, date(2026, 12, 31))
    after = strong.cost_usd(1_000_000, 0, strong.price_change.effective)
    assert before == strong.price_per_1m_input_usd
    assert after == strong.price_change.price_per_1m_input_usd


class Answer(BaseModel):
    answer: str


@pytest.mark.anyio
async def test_switching_a_tiers_model_is_a_one_line_config_change(tmp_path: Path) -> None:
    """Phases.md Phase 2 exit criterion: edit ONE line of models.yaml, change no code."""
    original = MODELS_YAML.read_text(encoding="utf-8")
    current = load_registry(MODELS_YAML).model(Tier.CHEAP).id
    switched = original.replace(f"id: {current}", "id: gemini-switched-test-model", 1)
    diff = [
        line
        for line in difflib.unified_diff(original.splitlines(), switched.splitlines(), lineterm="")
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert len(diff) == 2, f"expected one changed line, got {diff}"  # one line out, one line in

    path = tmp_path / "models.yaml"
    path.write_text(switched, encoding="utf-8")
    registry = load_registry(path)
    completion = FakeCompletion(reply('{"answer": "ok"}'))
    gateway = LLMGateway(
        registry,
        FakeLedger(),  # type: ignore[arg-type]
        prompt_library(),
        api_key="test",
        timeout_s=5,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        completion=completion,
    )
    await gateway.structured(
        Answer, tier=Tier.CHEAP, prompt=judge(), ctx=ctx(), route_reason="test"
    )
    assert completion.calls[0]["model"] == "gemini/gemini-switched-test-model"
    # The other tiers are untouched.
    assert registry.model(Tier.STANDARD) == load_registry(MODELS_YAML).model(Tier.STANDARD)


def test_two_tiers_may_not_share_a_model() -> None:
    raw = make_registry().model_dump()["tiers"]
    raw["standard"]["id"] = raw["cheap"]["id"]
    with pytest.raises(ValidationError, match="same model"):
        make_registry().model_validate(
            {
                "verified_at": "2026-10-17",
                "rpd_resets_at": "00:00 America/Los_Angeles",
                "tiers": raw,
            }
        )


def test_a_missing_tier_is_refused() -> None:
    raw = make_registry().model_dump()["tiers"]
    del raw["strong"]
    with pytest.raises(ValidationError, match="'strong' is missing"):
        make_registry().model_validate(
            {
                "verified_at": "2026-10-17",
                "rpd_resets_at": "00:00 America/Los_Angeles",
                "tiers": raw,
            }
        )


CODE_DIRS = ["backend/app", "backend/scripts", "frontend/src", "frontend/e2e", "supabase"]


def test_no_model_id_appears_outside_the_registry() -> None:
    """Rules.md P5: code names tiers; only config/models.yaml names models."""
    registry = load_registry(MODELS_YAML)
    ids = [registry.model(t).id for t in Tier] + [load_embedding_config(MODELS_YAML).id]
    offenders = []
    for d in CODE_DIRS:
        for path in (REPO_ROOT / d).rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".sql", ".json", ".mjs"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders += [f"{path.relative_to(REPO_ROOT)}: {i}" for i in ids if i in text]
    assert offenders == []
