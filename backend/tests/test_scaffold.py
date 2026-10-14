"""Scaffold smoke test: the package layout required by Rules.md §3.1 imports cleanly."""

import importlib

import pytest

SUBPACKAGES = ["api", "core", "agents", "memory", "research", "llm", "models", "workers"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(f"app.{name}")
