"""Cases run BY tests/test_real_call_guard.py in a subprocess that has a provider key in its
environment (this file is not collected by the normal run: its name does not start with test_)."""

import asyncio
import os

import pytest

from tests.conftest import make_settings

# The refusal is `RealCallRefused(AssertionError)`; it is matched by base class and message because
# pytest imports the conftest under two module names, so the class object differs.


def test_the_provider_key_is_gone_from_the_environment_and_from_settings() -> None:
    assert "GOOGLE_API_KEY" not in os.environ
    assert make_settings().google_api_key is None


def test_a_real_provider_call_is_refused() -> None:
    import app.llm._litellm as provider

    with pytest.raises(AssertionError, match="real model-provider call"):
        asyncio.run(provider.acompletion(model="x", messages=[]))


def test_a_gateway_built_with_the_default_completion_reaches_the_refusal() -> None:
    from app.llm.gateway import LLMGateway
    from app.llm.prompts import library_from
    from app.llm.registry import registry_from
    from tests.llm_fakes import FakeLedger

    settings = make_settings()
    gateway = LLMGateway(
        registry_from(settings.models_config_path),
        FakeLedger(dev_budget=object()),  # type: ignore[arg-type]
        library_from(settings.prompts_dir),
        api_key=None,
        timeout_s=1.0,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=0.0,
    )
    with pytest.raises(AssertionError, match="real model-provider call"):
        asyncio.run(gateway._completion(model="x", messages=[]))


def test_the_real_encoder_is_refused() -> None:
    from app.memory.embeddings import default_encoder

    with pytest.raises(AssertionError, match="real encoder"):
        default_encoder()
