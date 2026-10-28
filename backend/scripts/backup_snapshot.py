"""Back up the evaluation snapshot to a second physical disk (Architecture.md section 6).

    PYTHONPATH=. uv run python scripts/backup_snapshot.py --store D:/store --to E:/brandverse-backup

Refuses a destination inside the repository, in a cloud-synced folder, or on the same physical
disk as the store; copies additively; hashes every file on both sides; writes BACKUP-VERIFIED.json.
Run it after every recording session that committed requests. Exit status is 0 only if the copy
verified.
"""

import argparse
import sys
from pathlib import Path

from app.research.snapshot_backup import UnsuitableBackupDestination, backup_snapshot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--to", type=Path, required=True)
    args = ap.parse_args()
    try:
        report = backup_snapshot(args.store, args.to)
    except UnsuitableBackupDestination as e:
        print(f"REFUSED: {e}")
        return 2
    print(
        f"copied {report.copied}, already there {report.already_there}, "
        f"verified {report.verified}, problems {len(report.problems)}"
    )
    for problem in report.problems:
        print(f"  PROBLEM: {problem}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
