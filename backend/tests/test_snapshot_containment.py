"""Raw page bodies exist only inside the evaluation namespace (Architecture.md section 6; owner,
2026-10-28). A containment test that has never been seen to fire is not known to contain anything,
so this file proves the detector FIRES before it is trusted to pass:

1. `find_raw_bodies` is shown to find a body planted in a non-evaluation path, as plain text, inside
   gzip, and nested, and to ignore the evaluation namespace.
2. The real pipeline (record, commit, replay, the driver, the live fetcher path) is run with a
   unique sentinel in every page body and the whole workspace, the repository tree and the pytest
   temp area are scanned: nothing outside the namespace may hold it.
3. The same containment check is run against a store MUTATED to leak one copy of every body to a
   stray path, and must raise. That is the proof the check can fail on the real code path, not just
   on a planted file.
"""

import gzip
from pathlib import Path

import pytest

from app.research import snapshot as snapshot_module
from app.research.driver import Mode, research
from app.research.snapshot import (
    REPO_ROOT,
    SnapshotPages,
    SnapshotSearch,
    SnapshotStore,
    Staging,
    find_raw_bodies,
    record_request,
)
from tests.snapshot_support import (
    CFG,
    DECLARED,
    EXTRACTOR,
    TODAY,
    LivePages,
    LiveSearch,
    body_with,
    fresh_sentinel,
    no_sleep,
    plan_for,
    urls_for,
)

pytestmark = pytest.mark.anyio


def assert_contained(roots: list[Path], store_root: Path, sentinel: str) -> None:
    stray = find_raw_bodies(roots, sentinel, allowed=store_root)
    assert stray == [], f"raw page bodies found outside the evaluation namespace: {stray}"


# ── 1. The detector fires ────────────────────────────────────────────────────────────────────


def test_the_detector_finds_a_body_planted_outside_the_namespace(tmp_path: Path):
    SENTINEL = fresh_sentinel()
    store_root = tmp_path / "evaluation-snapshot"
    store_root.mkdir()
    (store_root / "inside.gz").write_bytes(gzip.compress(SENTINEL.encode()))  # allowed
    stray = tmp_path / "elsewhere"
    stray.mkdir()
    (stray / "page.html").write_text(f"<html>{SENTINEL}</html>")
    with pytest.raises(AssertionError, match="outside the evaluation namespace"):
        assert_contained([tmp_path], store_root, SENTINEL)
    assert find_raw_bodies([tmp_path], SENTINEL, store_root) == [stray / "page.html"]


def test_the_detector_finds_a_gzipped_body_and_a_deeply_nested_one(tmp_path: Path):
    SENTINEL = fresh_sentinel()
    store_root = tmp_path / "evaluation-snapshot"
    store_root.mkdir()
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "cache.bin").write_bytes(gzip.compress(f"x {SENTINEL} y".encode()))
    (tmp_path / "plain.txt").write_text("nothing here")
    found = find_raw_bodies([tmp_path], SENTINEL, store_root)
    assert found == [deep / "cache.bin"]


def test_the_detector_ignores_the_namespace_and_finds_nothing_when_nothing_leaked(tmp_path: Path):
    SENTINEL = fresh_sentinel()
    store_root = tmp_path / "evaluation-snapshot"
    (store_root / "bodies").mkdir(parents=True)
    (store_root / "bodies" / "x.gz").write_bytes(gzip.compress(SENTINEL.encode()))
    (store_root / "notes.txt").write_text(SENTINEL)
    assert_contained([tmp_path], store_root, SENTINEL)  # no raise


# ── 2. The real pipeline stays inside ────────────────────────────────────────────────────────


async def run_pipeline(store: SnapshotStore, sentinel: str) -> None:
    plan = plan_for()
    urls = urls_for(plan)
    bodies = {u: body_with(sentinel) for us in urls.values() for u in us}
    report = await record_request(
        "R01",
        plan,
        store=store,
        search=LiveSearch(plan, urls),
        pages=LivePages(bodies),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=2,
        today=TODAY,
        sleep=no_sleep,
    )
    assert report.outcome == "committed"
    replayed = await research(
        plan,
        "D2",
        mode=Mode.PRODUCTION,
        search=SnapshotSearch(store, DECLARED),
        pages=SnapshotPages(store),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        today=TODAY,
    )
    assert replayed.findings  # the sentinel really went through the whole path
    assert any(sentinel in f.page.text for f in replayed.findings)


def scan_roots(workspace: Path, tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    return [workspace, REPO_ROOT, tmp_path_factory.getbasetemp()]


async def test_record_and_replay_write_no_raw_body_outside_the_namespace(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = fresh_sentinel()
    store = SnapshotStore.create(workspace / "evaluation-snapshot")
    await run_pipeline(store, sentinel)
    # the bodies ARE in the store (otherwise this proves nothing) ...
    assert find_raw_bodies([store.root], sentinel, allowed=workspace / "elsewhere")
    # ... and nowhere else: not the workspace, not the repository, not the pytest temp area
    assert_contained(scan_roots(workspace, tmp_path_factory), store.root, sentinel)


# ── 3. The check fires on the real code path when the store leaks ────────────────────────────


async def test_the_containment_check_fails_against_a_store_that_leaks_a_copy(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stray_dir = workspace / "stray"
    stray_dir.mkdir()
    original = Staging.put_body

    def leaking_put_body(self: Staging, html: str) -> str:
        sha = original(self, html)
        (stray_dir / f"{sha[:8]}.html").write_text(html)  # the leak: one plain-text copy
        return sha

    monkeypatch.setattr(snapshot_module.Staging, "put_body", leaking_put_body)
    sentinel = fresh_sentinel()
    store = SnapshotStore.create(workspace / "evaluation-snapshot")
    await run_pipeline(store, sentinel)
    with pytest.raises(AssertionError, match="outside the evaluation namespace"):
        assert_contained(scan_roots(workspace, tmp_path_factory), store.root, sentinel)


async def test_the_containment_check_also_fails_against_a_gzipped_leak(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stray_dir = workspace / "backup-cache"
    stray_dir.mkdir()
    original = Staging.put_body

    def leaking_put_body(self: Staging, html: str) -> str:
        sha = original(self, html)
        (stray_dir / f"{sha[:8]}.bin").write_bytes(gzip.compress(html.encode()))
        return sha

    monkeypatch.setattr(snapshot_module.Staging, "put_body", leaking_put_body)
    sentinel = fresh_sentinel()
    store = SnapshotStore.create(workspace / "evaluation-snapshot")
    await run_pipeline(store, sentinel)
    with pytest.raises(AssertionError, match="outside the evaluation namespace"):
        assert_contained(scan_roots(workspace, tmp_path_factory), store.root, sentinel)
