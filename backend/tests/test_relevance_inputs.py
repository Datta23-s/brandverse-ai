"""The relevance set's frozen inputs: queries.md is the source, queries.yaml its derivation, and the
locks make both unchangeable (docs/evaluation-plan.md §7)."""

import json
from pathlib import Path

import pytest
import yaml

from app.evaluation.relevance import parse_queries_md, sha256_file, verify_frozen
from scripts.freeze_relevance_inputs import AlreadyFrozen, freeze_queries

RELEVANCE = Path(__file__).resolve().parents[2] / "evaluation" / "relevance"

SAMPLE = """\
METTLEPAN
1. What do we say
2. How do we sound
ZARVIK NUTRITION
3. What is in the mix
UNANSWERABLE
4. What did we spend [UNANSWERABLE]
"""


def test_parse_assigns_ids_brands_and_strips_the_marker() -> None:
    rows = parse_queries_md(SAMPLE)
    assert rows == [
        {"id": "q01", "brand": "mettlepan", "text": "What do we say", "unanswerable": False},
        {"id": "q02", "brand": "mettlepan", "text": "How do we sound", "unanswerable": False},
        {"id": "q03", "brand": "zarvik", "text": "What is in the mix", "unanswerable": False},
        {"id": "q04", "brand": None, "text": "What did we spend", "unanswerable": True},
    ]


@pytest.mark.parametrize(
    "text",
    [
        SAMPLE.replace("3. What is", "5. What is"),  # numbering skips
        SAMPLE.replace("[UNANSWERABLE]", ""),  # unmarked under UNANSWERABLE
        SAMPLE.replace(
            "2. How do we sound", "2. How do we sound [UNANSWERABLE]"
        ),  # marked elsewhere
        "1. before any heading\n",
    ],
    ids=["numbering", "unmarked", "marked_elsewhere", "no_heading"],
)
def test_parse_refuses_an_inconsistent_file(text: str) -> None:
    with pytest.raises(ValueError):
        parse_queries_md(text)


def test_a_frozen_input_that_changes_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a", encoding="utf-8")
    lock = tmp_path / "x.lock"
    with pytest.raises(FileNotFoundError):
        verify_frozen(lock, x=f)
    lock.write_text(json.dumps({"sha256": {"x": sha256_file(f)}}), encoding="utf-8")
    verify_frozen(lock, x=f)
    f.write_text("b", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        verify_frozen(lock, x=f)


def test_a_lock_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "queries.md").write_text(SAMPLE, encoding="utf-8")
    freeze_queries(tmp_path)
    with pytest.raises(AlreadyFrozen):
        freeze_queries(tmp_path)


def test_the_committed_queries_are_fifty_with_eight_unanswerable() -> None:
    rows = parse_queries_md((RELEVANCE / "queries.md").read_text(encoding="utf-8"))
    assert len(rows) == 50
    assert [r["id"] for r in rows if r["unanswerable"]] == [f"q{n}" for n in range(43, 51)]


def test_queries_yaml_is_exactly_what_queries_md_derives_and_both_match_the_lock() -> None:
    md, derived = RELEVANCE / "queries.md", RELEVANCE / "queries.yaml"
    assert yaml.safe_load(derived.read_text(encoding="utf-8")) == parse_queries_md(
        md.read_text(encoding="utf-8")
    )
    verify_frozen(RELEVANCE / "queries.lock", md=md, yaml=derived)
