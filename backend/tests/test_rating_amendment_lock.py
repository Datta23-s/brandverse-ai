"""Rubric amendment A1 cannot be edited after it is frozen (docs/rating/rubric-amendment-1.md).

Until the lock exists the amendment is a draft and the check on the real file is skipped by
design; the mechanism is exercised on temporary files, so it is tested before it is used.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.freeze_rating_amendment import AMENDMENT, LOCK, freeze, normalised_sha256


def test_the_real_amendment_matches_its_lock_once_it_is_frozen() -> None:
    if not LOCK.exists():
        pytest.skip("the amendment is a draft: it has not been frozen yet")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert lock["file"] == AMENDMENT.name
    assert lock["frozen_by"] and lock["frozen_utc"]
    assert normalised_sha256(AMENDMENT) == lock["sha256"], (
        "rubric-amendment-1.md changed after it was frozen: write amendment A2, do not edit A1"
    )


def test_freezing_records_the_hash_the_time_and_the_freezer(tmp_path: Path) -> None:
    amendment, lock = tmp_path / "a.md", tmp_path / "a.lock"
    amendment.write_text("anchors\n", encoding="utf-8", newline="\n")
    when = datetime(2026, 11, 7, 9, 30, tzinfo=UTC)
    record = freeze(amendment, lock, "the owner", now=when)
    assert record["frozen_utc"] == "2026-11-07T09:30:00+00:00"
    assert json.loads(lock.read_text(encoding="utf-8")) == record


def test_a_frozen_amendment_cannot_be_frozen_again(tmp_path: Path) -> None:
    amendment, lock = tmp_path / "a.md", tmp_path / "a.lock"
    amendment.write_text("anchors\n", encoding="utf-8")
    freeze(amendment, lock, "x")
    with pytest.raises(SystemExit, match="already frozen"):
        freeze(amendment, lock, "y")


def test_an_edit_after_the_freeze_changes_the_hash_and_line_endings_do_not(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_bytes(b"one\ntwo\n")
    frozen = normalised_sha256(f)
    f.write_bytes(b"one\r\ntwo\r\n")  # a Windows checkout of the same text
    assert normalised_sha256(f) == frozen
    f.write_bytes(b"one\ntwo changed\n")
    assert normalised_sha256(f) != frozen
