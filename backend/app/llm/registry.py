"""The model registry: config/models.yaml, validated, and the only source of model IDs.

Rules.md P5: no model ID appears anywhere else in the repository; code names a TIER, and the
registry turns it into a model. Switching a tier's model is therefore a one-line change — the
tier's `id:` in config/models.yaml — with no code change (Phases.md Phase 2 exit criterion;
tests/test_llm_registry.py proves it).

Prices are paid-tier LIST prices from the registry, used for the list-price-equivalent cost
(Rules.md C2, C3); LiteLLM's own price table is not used, so the figures trace to this file.
"""

from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Tier(StrEnum):
    """Routable tiers (Architecture.md §4.1). `media` and `fallback` are not routable yet."""

    CHEAP = "cheap"
    STANDARD = "standard"
    STRONG = "strong"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class RateLimits(_Strict):
    rpm: int = Field(gt=0)
    tpm: int = Field(gt=0)
    rpd: int = Field(gt=0)


class PriceChange(_Strict):
    effective: date
    price_per_1m_input_usd: float = Field(ge=0)
    price_per_1m_output_usd: float = Field(ge=0)


class TierModel(_Strict):
    provider: str
    id: str = Field(min_length=1)
    rate_limits: RateLimits
    price_per_1m_input_usd: float = Field(ge=0)
    price_per_1m_output_usd: float = Field(ge=0)
    price_change: PriceChange | None = None

    @property
    def litellm_model(self) -> str:
        """LiteLLM's model string: `<provider>/<id>` (e.g. the Gemini API provider is `gemini`)."""
        return f"{self.provider}/{self.id}"

    def cost_usd(self, input_tokens: int, output_tokens: int, on: date) -> float:
        """List-price-equivalent cost (not money paid: the project uses the free tier)."""
        price_in, price_out = self.price_per_1m_input_usd, self.price_per_1m_output_usd
        if self.price_change is not None and on >= self.price_change.effective:
            price_in = self.price_change.price_per_1m_input_usd
            price_out = self.price_change.price_per_1m_output_usd
        return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class ModelRegistry(_Strict):
    verified_at: date
    rpd_resets_at: str
    tiers: dict[str, dict[str, object]]
    routable: dict[Tier, TierModel] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _resolve_routable(self) -> "ModelRegistry":
        resolved: dict[Tier, TierModel] = {}
        for tier in Tier:
            raw = self.tiers.get(tier.value)
            if raw is None:
                raise ValueError(f"config/models.yaml: tier {tier.value!r} is missing")
            resolved[tier] = TierModel.model_validate(raw)
        ids = [m.id for m in resolved.values()]
        # One tier, one model; and no model shared by two tiers (it would merge their quotas).
        if len(set(ids)) != len(ids):
            raise ValueError("config/models.yaml: two tiers name the same model")
        object.__setattr__(self, "routable", resolved)
        return self

    def model(self, tier: Tier) -> TierModel:
        return self.routable[tier]

    def tier_of(self, model_id: str) -> Tier | None:
        return next((t for t, m in self.routable.items() if m.id == model_id), None)


def load_registry(path: Path) -> ModelRegistry:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a mapping")
    return ModelRegistry.model_validate(document)


@lru_cache(maxsize=4)
def registry_from(path: Path) -> ModelRegistry:
    """Loaded once per path per process (config is read once, Rules.md §3.1)."""
    return load_registry(path)
