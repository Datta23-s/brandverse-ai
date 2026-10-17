"""Versioned prompt templates: files with explicit versions, immutable once released."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.llm.gateway import REPAIR_PROMPT
from app.llm.prompts import LOCK_FILE, PromptRef, PromptTemplate, file_sha256, load_library

PROMPTS = get_settings().prompts_dir


def copy_prompts(tmp_path: Path) -> Path:
    target = tmp_path / "prompts"
    shutil.copytree(PROMPTS, target)
    return target


def add_version(directory: Path, body: str, *, lock: bool = True) -> Path:
    path = directory / "smoke_capital_city" / "v2.yaml"
    path.write_text(body, encoding="utf-8")
    if lock:
        with (directory / LOCK_FILE).open("a", encoding="utf-8") as f:
            f.write(f"smoke_capital_city v2 sha256:{file_sha256(path)}\n")
    return path


V2 = """id: smoke_capital_city
version: v2
description: Second version.
variables: [country]
system: null
user: Name the capital of $country.
"""


def test_the_repositorys_prompts_load_and_match_their_lock() -> None:
    library = load_library(PROMPTS)
    assert PromptRef(*REPAIR_PROMPT) in library.refs()
    assert len(library.refs()) == len(list(PROMPTS.glob("*/*.yaml")))


def test_rendering_fills_exactly_the_declared_variables() -> None:
    template = load_library(PROMPTS).get("smoke_capital_city", "v1")
    rendered = template.render(country="Portugal")
    assert rendered.ref == PromptRef("smoke_capital_city", "v1")
    assert rendered.user == "What is the capital of Portugal?"
    with pytest.raises(ValueError, match="takes"):
        template.render()
    with pytest.raises(ValueError, match="takes"):
        template.render(country="Portugal", extra="x")


def test_a_value_is_inserted_literally_never_expanded() -> None:
    rendered = load_library(PROMPTS).get("smoke_capital_city", "v1").render(country="$x {y}")
    assert rendered.user == "What is the capital of $x {y}?"


def test_editing_a_released_version_in_place_is_refused(tmp_path: Path) -> None:
    directory = copy_prompts(tmp_path)
    path = directory / "smoke_capital_city" / "v1.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("What is the capital", "What is the capitol"),
        "utf-8",
    )
    with pytest.raises(ValueError, match="never edited"):
        load_library(directory)


def test_line_endings_do_not_change_the_hash(tmp_path: Path) -> None:
    """A Windows checkout (CRLF) must hash as CI's Linux checkout (LF) does."""
    directory = copy_prompts(tmp_path)
    path = directory / "smoke_capital_city" / "v1.yaml"
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    load_library(directory)


def test_a_new_version_loads_beside_the_old_one(tmp_path: Path) -> None:
    directory = copy_prompts(tmp_path)
    add_version(directory, V2)
    library = load_library(directory)
    assert library.get("smoke_capital_city", "v1").render(country="X").user.startswith("What")
    assert library.get("smoke_capital_city", "v2").render(country="X").user.startswith("Name")


def test_a_version_missing_from_the_lock_is_refused(tmp_path: Path) -> None:
    directory = copy_prompts(tmp_path)
    add_version(directory, V2, lock=False)
    with pytest.raises(ValueError, match="not in versions.lock"):
        load_library(directory)


def test_a_locked_version_with_no_file_is_refused(tmp_path: Path) -> None:
    directory = copy_prompts(tmp_path)
    add_version(directory, V2).unlink()
    with pytest.raises(ValueError, match="no file"):
        load_library(directory)


def test_the_file_must_name_the_id_and_version_its_path_says(tmp_path: Path) -> None:
    directory = copy_prompts(tmp_path)
    add_version(directory, V2.replace("version: v2", "version: v3"))
    with pytest.raises(ValueError, match="its path says otherwise"):
        load_library(directory)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"user": "Capital of $country and $city?"}, "placeholders"),
        ({"variables": ("country", "unused")}, "placeholders"),
        ({"user": "Costs $5 in $country"}, "malformed placeholder"),
        ({"version": "2"}, "version"),
        ({"id": "Smoke-Capital"}, "id"),
    ],
)
def test_malformed_templates_are_refused(changes: dict[str, object], match: str) -> None:
    fields: dict[str, object] = {
        "id": "smoke_capital_city",
        "version": "v1",
        "description": "d",
        "variables": ("country",),
        "system": None,
        "user": "Capital of $country?",
    } | changes
    with pytest.raises(ValidationError, match=match):
        PromptTemplate.model_validate(fields)
