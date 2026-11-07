"""Freeze rubric amendment A1: write its hash and the time to a lock file, once.

`docs/rating/rubric-amendment-1.md` is frozen before the 8 November sitting and before either rater
sees any evaluation copy (the amendment, section 5). Freezing is this: the SHA-256 of the file with
line endings normalised, the UTC time, and who froze it, in `rubric-amendment-1.lock`. It refuses to
run twice. `tests/test_rating_amendment_lock.py` fails if the file is edited afterwards: a change
after the freeze is a new amendment, never an edit (`docs/approval-rubric.md` section 7).

Usage (from backend/):  uv run python -m scripts.freeze_rating_amendment --by "<name>"
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RATING = Path(__file__).resolve().parents[2] / "docs" / "rating"
AMENDMENT = RATING / "rubric-amendment-1.md"
LOCK = RATING / "rubric-amendment-1.lock"
# A2 (owner, 7 November 2026): frozen before the evaluation generates anything. The instrument the
# raters hold is the two SHEETS as much as the text, so the lock covers all three files; a change
# to any of them after the freeze is A3, never an edit.
AMENDMENT_2 = RATING / "rubric-amendment-2.md"
LOCK_2 = RATING / "rubric-amendment-2.lock"
COMPANIONS_2 = (RATING / "rubric-sheet.html", RATING / "scoring-sheet.html")


def normalised_sha256(path: Path) -> str:
    """The file's hash with CRLF read as LF, so a Windows and a Linux checkout agree."""
    text = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def freeze(
    amendment: Path,
    lock: Path,
    by: str,
    now: datetime | None = None,
    companions: tuple[Path, ...] = (),
) -> dict[str, Any]:
    if lock.exists():
        raise SystemExit(f"already frozen: {lock} exists. A change is a new amendment.")
    record: dict[str, Any] = {
        "file": amendment.name,
        "sha256": normalised_sha256(amendment),
        "frozen_utc": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "frozen_by": by,
    }
    if companions:
        record["companions"] = {p.name: normalised_sha256(p) for p in companions}
    lock.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--by", required=True, help="who is freezing it")
    parser.add_argument("--amendment", choices=("1", "2"), default="1")
    args = parser.parse_args()
    if args.amendment == "2":
        record = freeze(AMENDMENT_2, LOCK_2, args.by, companions=COMPANIONS_2)
    else:
        record = freeze(AMENDMENT, LOCK, args.by)
    print("frozen:", json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
