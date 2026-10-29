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

The same-disk refusal alone can be overridden, deliberately (owner, 2026-10-28: the backup is a
folder on the same physical disk). The refusal is NOT removed: `backup_snapshot` raises unless the
caller passes `same_disk_acknowledgement` equal, character for character, to `SAME_DISK_SENTENCE`,
which the command-line tool prints and makes the person type back. Each use is appended to the
store's waivers log and written into the backup's certificate (`same_disk: true`), so a same-disk
copy can never be mistaken for an off-device one. The other refusals (repository, cloud-synced
folder, overlap, a disk it cannot identify) have no override.
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
WAIVERS = "backup-waivers.jsonl"
# Files that only ever grow: the backup's copy must be a byte prefix of the store's (truncation or
# divergence is refused, growth is allowed). The waiver log is one: a line is added at every launch
# that uses the same-disk override (owner, 2026-10-29: a session's backup ended ok=False for it).
APPEND_ONLY = (*INDEX_FILES, WAIVERS)
NEWLINE = chr(10)
SAME_DISK_SENTENCE = (
    "A same-disk copy protects against accidental deletion of the store and not against disk "
    "failure: if this disk fails, the snapshot is unrecoverable and the evaluation's results "
    "become unreproducible, because the web state cannot be re-captured."
)


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
    same_disk_acknowledgement: str | None = None,
) -> bool:
    """Refuse a destination that would defeat the purpose or break a condition. Returns True when
    the destination is on the same physical disk and that was deliberately acknowledged, else
    False."""
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
        if same_disk_acknowledgement == SAME_DISK_SENTENCE:
            return True
        raise UnsuitableBackupDestination(
            f"{dest} is on the same physical disk (disk {here}) as the store: a copy that dies "
            "with the disk is not a backup. " + SAME_DISK_SENTENCE
        )
    return False


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
    same_disk_acknowledgement: str | None = None,
) -> BackupReport:
    store = SnapshotStore(source)  # refuses anything that is not an evaluation store
    same_disk = check_destination(
        store.root, dest, disk_of=disk_of, same_disk_acknowledgement=same_disk_acknowledgement
    )
    if same_disk:
        _log_waiver(store.root, dest)
    dest = dest.resolve()
    report = BackupReport()
    if dest.exists() and any(dest.iterdir()) and not (dest / MARKER).is_file():
        raise UnsuitableBackupDestination(f"{dest} is not empty and is not a backup of a store")
    dest.mkdir(parents=True, exist_ok=True)
    for rel in _files(store.root):
        src, dst = store.root / rel, dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if rel.name in APPEND_ONLY:
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
            "same_disk": same_disk,
        }
        if same_disk:
            verified["WARNING"] = SAME_DISK_SENTENCE
        (dest / VERIFIED).write_text(json.dumps(verified, indent=1) + "\n", encoding="utf-8")
    return report


def obtain_acknowledgement(
    allow_same_disk: bool,
    typed: str | None = None,
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> str | None:
    """The deliberate act behind `--allow-same-disk`: print the sentence and require it back.

    No flag: nothing is acknowledged (None) and the refusal stands. With the flag the sentence is
    printed and the person must type it exactly (`typed` supplies it for a job that has no
    keyboard; the flag is still required, and the text must still match). Anything else, including
    a closed stdin, raises: the override is never a default and never a side effect."""
    if not allow_same_disk:
        return None
    say("WARNING: " + SAME_DISK_SENTENCE)
    try:
        answer = typed if typed is not None else ask("Type that sentence exactly to confirm: ")
    except EOFError:
        answer = None
    if answer != SAME_DISK_SENTENCE:
        raise UnsuitableBackupDestination(
            "the same-disk acknowledgement was not given exactly; nothing was done"
        )
    return answer


def _log_waiver(store_root: Path, dest: Path) -> None:
    """Every use of the same-disk override is on the record, in the store itself."""
    line = {
        "utc": datetime.now(UTC).isoformat(),
        "destination": str(dest.resolve()),
        "acknowledged": SAME_DISK_SENTENCE,
    }
    with (store_root / WAIVERS).open("a", encoding="utf-8", newline=NEWLINE) as f:
        f.write(json.dumps(line) + NEWLINE)


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
