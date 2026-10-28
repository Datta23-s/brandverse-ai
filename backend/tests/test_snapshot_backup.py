"""The snapshot backup: a verified copy, and a destination that cannot defeat the purpose.

The condition (owner, 2026-10-28): retained on the laptop plus one backup, and the corpus not
shared, so an external drive or a second local disk, never a cloud service. The destination checks
have no switch; each is shown to refuse, and the copy is shown to catch a corrupted or diverged
backup.
"""

import json
from pathlib import Path

import pytest

from app.research.snapshot import REPO_ROOT, SnapshotStore, record_request
from app.research.snapshot_backup import (
    VERIFIED,
    UnsuitableBackupDestination,
    backup_snapshot,
    check_destination,
    verify_backup,
)
from tests.snapshot_support import (
    CFG,
    DECLARED,
    EXTRACTOR,
    TODAY,
    LivePages,
    LiveSearch,
    no_sleep,
    plan_for,
    urls_for,
)

pytestmark = pytest.mark.anyio


def two_disks(path: Path) -> int | None:
    """The source tree is 'disk 0'; anything under a directory named external is 'disk 1'."""
    return 1 if "external" in [p.casefold() for p in path.parts] else 0


async def make_store(tmp_path: Path, request_id: str = "R01", category="neckband earphones"):
    store = SnapshotStore.create(tmp_path / "laptop" / "evaluation-snapshot")
    await commit(store, request_id, category)
    return store


async def commit(store: SnapshotStore, request_id: str, category="neckband earphones"):
    plan = plan_for(category)
    await record_request(
        request_id,
        plan,
        store=store,
        search=LiveSearch(plan, urls_for(plan)),
        pages=LivePages(),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=2,
        today=TODAY,
        sleep=no_sleep,
    )


async def test_a_backup_copies_and_verifies_every_file_and_writes_its_certificate(tmp_path):
    store = await make_store(tmp_path)
    dest = tmp_path / "external" / "backup"
    report = await _run(store, dest)
    assert report.ok and report.copied > 0 and report.verified == report.copied
    certificate = json.loads((dest / VERIFIED).read_text())
    assert certificate["manifest"]["sha256"] == store.manifest_summary()["sha256"]
    assert certificate["files"] == report.verified
    assert not (dest / "staging").exists()
    assert (dest / ".evaluation-namespace").is_file()  # the backup is itself an evaluation store


async def _run(store, dest):
    return backup_snapshot(store.root, dest, disk_of=two_disks)


async def test_a_second_run_copies_nothing_and_a_later_commit_is_added_incrementally(tmp_path):
    store = await make_store(tmp_path)
    dest = tmp_path / "external" / "backup"
    first = await _run(store, dest)
    again = await _run(store, dest)
    assert again.ok and again.copied == 0 and again.already_there == first.verified
    await commit(store, "R04", "smartwatch")
    later = await _run(store, dest)
    assert later.ok and later.copied > 0
    assert verify_backup(store.root, dest) == []


async def test_a_corrupted_copy_is_caught_by_the_verification(tmp_path):
    store = await make_store(tmp_path)
    dest = tmp_path / "external" / "backup"
    await _run(store, dest)
    victim = next((dest / "bodies").rglob("*.gz"))
    victim.write_bytes(b"bit rot")
    problems = verify_backup(store.root, dest)
    assert problems and "hash differs" in problems[0]
    (dest / "pages.jsonl").unlink()
    assert any("missing from the backup" in p for p in verify_backup(store.root, dest))


async def test_a_diverged_index_is_reported_and_never_overwritten(tmp_path):
    store = await make_store(tmp_path)
    dest = tmp_path / "external" / "backup"
    await _run(store, dest)
    (dest / "committed.jsonl").write_text('{"request": "ROGUE"}\n')
    report = await _run(store, dest)
    assert not report.ok and any("diverged" in p for p in report.problems)
    assert (dest / "committed.jsonl").read_text() == '{"request": "ROGUE"}\n'  # left as found


# ── The destinations it refuses, each shown to refuse ───────────────────────────────────────


async def test_it_refuses_a_destination_on_the_same_physical_disk(tmp_path):
    store = await make_store(tmp_path)
    with pytest.raises(UnsuitableBackupDestination, match="same physical disk"):
        backup_snapshot(store.root, tmp_path / "laptop" / "copy", disk_of=two_disks)
    assert not (tmp_path / "laptop" / "copy").exists()  # refused before anything was made


async def test_it_refuses_when_it_cannot_tell_which_disk_a_path_is_on(tmp_path):
    store = await make_store(tmp_path)
    with pytest.raises(UnsuitableBackupDestination, match="cannot tell"):
        backup_snapshot(store.root, tmp_path / "external" / "b", disk_of=lambda p: None)


@pytest.mark.parametrize(
    "folder", ["OneDrive", "Dropbox", "Google Drive", "iCloudDrive", "OneDrive - Personal", "MEGA"]
)
async def test_it_refuses_a_cloud_synced_folder(tmp_path, folder):
    store = await make_store(tmp_path)
    dest = tmp_path / "external" / folder / "backup"
    with pytest.raises(UnsuitableBackupDestination, match="cloud-synced"):
        backup_snapshot(store.root, dest, disk_of=two_disks)
    assert not dest.exists()


async def test_it_refuses_a_destination_inside_the_repository_or_inside_the_store(tmp_path):
    store = await make_store(tmp_path)
    with pytest.raises(UnsuitableBackupDestination, match="inside the repository"):
        check_destination(store.root, REPO_ROOT / "evaluation" / "backup", disk_of=two_disks)
    with pytest.raises(UnsuitableBackupDestination, match="overlaps"):
        check_destination(store.root, store.root / "inner", disk_of=two_disks)


async def test_it_refuses_a_non_empty_destination_that_is_not_a_backup_and_a_non_store_source(
    tmp_path,
):
    store = await make_store(tmp_path)
    busy = tmp_path / "external" / "busy"
    busy.mkdir(parents=True)
    (busy / "someone-elses.txt").write_text("x")
    with pytest.raises(UnsuitableBackupDestination, match="not empty"):
        await _run(store, busy)
    plain = tmp_path / "laptop" / "plain"
    plain.mkdir()
    with pytest.raises(Exception, match="no .evaluation-namespace"):
        backup_snapshot(plain, tmp_path / "external" / "b", disk_of=two_disks)
