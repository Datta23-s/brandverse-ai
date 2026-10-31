"""Verify the hash chain on `approvals` and `audit_log` and print each head, for anchoring.

The chain (migration 0042) detects an edited, deleted or reordered row; it cannot show that the
LAST rows were not removed, or that the table owner did not rewrite the whole chain. The defence is
a head hash kept OUTSIDE the database: commit this script's output to git with the weekly `pg_dump`
(Architecture.md section 9, hardening decision 2). A later head must extend an earlier one: the
sequence number only grows, and the chain from an anchored row onward must still verify.

Exit status 1 if either chain fails verification. Read-only.

Usage (from backend/):  uv run python -m scripts.chain_head [--url postgresql://...]
"""

import argparse
import sys
from datetime import UTC, datetime

import psycopg

from app.core.config import get_settings

TABLES = ("approvals", "audit_log")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--url", help="database URL (default: DATABASE_URL from the settings)")
    args = parser.parse_args()
    url = args.url
    if url is None:
        settings = get_settings()
        if settings.database_url is None:
            print("DATABASE_URL is not set")
            return 2
        url = settings.database_url.get_secret_value()
    failed = False
    print(f"# chain heads read {datetime.now(UTC).isoformat()}")
    with psycopg.connect(url, autocommit=True) as conn:
        for table in TABLES:
            row = conn.execute("select * from app.chain_verify(%s)", (table,)).fetchone()
            if row is None:
                raise RuntimeError(f"chain_verify returned no row for {table}")
            chained, unchained, head_seq, head_hash, first_bad, ok = row
            print(
                f"{table} rows_chained={chained} rows_unchained={unchained} "
                f"head_seq={head_seq} head_hash={head_hash} ok={ok}"
            )
            if not ok:
                failed = True
                print(f"  CHAIN BROKEN at chain_seq {first_bad}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
