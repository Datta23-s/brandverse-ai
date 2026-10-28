"""The backup of the evaluation snapshot (Architecture.md section 6; owner, 2026-10-28).

Retention "on the laptop plus one backup" is one of the four conditions of the zero-retention
exception, so the backup is a condition, not a to-do, and it is constrained by the fourth condition,
that the corpus is not shared: **an external drive or a second local disk, never a cloud service.**
Roughly 0.7 GB of third-party page bodies on someone else's servers is a different thing from a
working corpus on the researcher's own machine, whatever the privacy settings.

So the destination is checked before anything is copied, and the check has no switch to turn it off:

* not inside the repository (third-party content);
* not inside a cloud-synced folder (OneDrive, Dropbox, Google Drive, iCloud, Box, Nextcloud, Sync,
  pCloud, MEGA): a folder those clients watch would upload what is put in it;
* not on the same physical disk as the store: a copy that dies with the disk is not a backup.

The copy is additive and verified: bodies are immutable and content-addressed, the index files only
ever grow (the destination's version must be a prefix of the source's, otherwise the two have
diverged and nothing is overwritten), and after copying every file is hashed on both sides. The
destination gets a `BACKUP-VERIFIED.json` naming what was verified and when. Run it after every
session that commits requests.
"""

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.research.snapshot import MARKER, REPO_ROOT, SnapshotError, SnapshotStore

CLOUD_FOLDER_NAMES = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "icloud",
    "iclouddrive",
    "box sync",
    "box",
    "nextcloud",
    "owncloud",
    "sync.com",
    "pcloud",
    "mega",
    "megasync",
    "tresorit",
)
INDEX_FILES = ("committed.jsonl", "searches.jsonl", "pages.jsonl")
VERIFIED = "BACKUP-VERIFIED.json"


class UnsuitableBackupDestination(SnapshotError):
    pass


def powershell_disk_number(path: Path) -> int | None:
    """The physical disk a path's drive letter lives on, or None if it cannot be determined."""
    letter = path.drive.rstrip(r":\/").upper()
    if len(letter) != 1:
        return None
    exe = shutil.which("powershell")
    if exe is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed arguments, a letter we validated
            [
                exe,
                "-NoProfile",
                "-Command",
                f"(Get-Partition -DriveLetter {letter} | Get-Disk).Number",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def check_destination(
    source: Path,
    dest: Path,
    *,
    disk_of: Callable[[Path], int | None] = powershell_disk_number,
) -> None:
    """Refuse a destination that would defeat the purpose or break a condition."""
    source, dest = source.resolve(), dest.resolve()
    if dest == REPO_ROOT or REPO_ROOT in dest.parents:
        raise UnsuitableBackupDestination(f"{dest} is inside the repository")
    if dest == source or source in dest.parents or dest in source.parents:
        raise UnsuitableBackupDestination(f"{dest} overlaps the store itself")
    lowered = [p.casefold() for p in dest.parts]
    for part in lowered:
        if any(part == name or part.startswith(name + " -") for name in CLOUD_FOLDER_NAMES):
            raise UnsuitableBackupDestination(
                f"{dest} is in a cloud-synced folder ({part!r}): the corpus is not shared"
            )
    here, there = disk_of(source), disk_of(dest)
    if here is None or there is None:
        raise UnsuitableBackupDestination(
            "cannot tell which physical disks the store and the destination are on; "
            "refusing rather than guessing"
        )
    if here == there:
        raise UnsuitableBackupDestination(
            f"{dest} is on the same physical disk (disk {here}) as the store: a copy that dies "
            "with the disk is not a backup"
        )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] != "staging" and rel.name != VERIFIED:
            out.append(rel)
    return out


@dataclass(slots=True)
class BackupReport:
    copied: int = 0
    already_there: int = 0
    verified: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def backup_snapshot(
    source: Path,
    dest: Path,
    *,
    disk_of: Callable[[Path], int | None] = powershell_disk_number,
) -> BackupReport:
    store = SnapshotStore(source)  # refuses anything that is not an evaluation store
    check_destination(store.root, dest, disk_of=disk_of)
    dest = dest.resolve()
    report = BackupReport()
    if dest.exists() and any(dest.iterdir()) and not (dest / MARKER).is_file():
        raise UnsuitableBackupDestination(f"{dest} is not empty and is not a backup of a store")
    dest.mkdir(parents=True, exist_ok=True)
    for rel in _files(store.root):
        src, dst = store.root / rel, dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if rel.name in INDEX_FILES:
                old, new = dst.read_bytes(), src.read_bytes()
                if not new.startswith(old):
                    report.problems.append(
                        f"{rel}: the backup's copy is not a prefix of the store's: they diverged, "
                        "nothing overwritten"
                    )
                    continue
                if old == new:
                    report.already_there += 1
                    continue
            elif _sha(src) == _sha(dst):
                report.already_there += 1
                continue
            else:
                report.problems.append(f"{rel}: differs from the backup's copy (write-once file)")
                continue
        shutil.copy2(src, dst)
        report.copied += 1
    report.problems += verify_backup(store.root, dest, report)
    if report.ok:
        verified: dict[str, Any] = {
            "verified_utc": datetime.now(UTC).isoformat(),
            "files": report.verified,
            "manifest": store.manifest_summary(),
        }
        (dest / VERIFIED).write_text(json.dumps(verified, indent=1) + "\n", encoding="utf-8")
    return report


def verify_backup(source: Path, dest: Path, report: BackupReport | None = None) -> list[str]:
    """Every store file exists in the backup with the same hash. Returns the problems found."""
    problems: list[str] = []
    verified = 0
    for rel in _files(source):
        src, dst = source / rel, dest / rel
        if not dst.is_file():
            problems.append(f"{rel}: missing from the backup")
        elif _sha(src) != _sha(dst):
            problems.append(f"{rel}: hash differs in the backup")
        else:
            verified += 1
    if report is not None:
        report.verified = verified
    return problems
