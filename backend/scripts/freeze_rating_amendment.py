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

RATING = Path(__file__).resolve().parents[2] / "docs" / "rating"
AMENDMENT = RATING / "rubric-amendment-1.md"
LOCK = RATING / "rubric-amendment-1.lock"


def normalised_sha256(path: Path) -> str:
    """The file's hash with CRLF read as LF, so a Windows and a Linux checkout agree."""
    text = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def freeze(amendment: Path, lock: Path, by: str, now: datetime | None = None) -> dict[str, str]:
    if lock.exists():
        raise SystemExit(f"already frozen: {lock} exists. A change is a new amendment (A2).")
    record = {
        "file": amendment.name,
        "sha256": normalised_sha256(amendment),
        "frozen_utc": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "frozen_by": by,
    }
    lock.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--by", required=True, help="who is freezing it")
    record = freeze(AMENDMENT, LOCK, parser.parse_args().by)
    print("frozen:", json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
