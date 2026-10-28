"""The evaluation snapshot: search results and page bodies recorded once, replayed for every arm.

What must hold: a replay returns the recorded bytes whatever the live page does afterwards; a replay
never reaches the network and a miss is an error; a request is captured whole or not at all; entries
are write-once and shared across requests; a permanent failure is frozen as an outcome, a transient
one is retried a bounded number of times and then frozen; the frozen-failed share is reported; a
degraded search is never recorded; the store refuses to live anywhere but an evaluation namespace
outside the repository.
"""

import json
from pathlib import Path

import pytest

from app.errors import FetchFailed, FetchRefused, FetchUnsupported, SearchRateLimited
from app.research.driver import Mode, research
from app.research.search import EngineFailure, SearchResponse
from app.research.snapshot import (
    REPO_ROOT,
    NotAnEvaluationNamespace,
    SnapshotError,
    SnapshotMiss,
    SnapshotPages,
    SnapshotSearch,
    SnapshotStore,
    record_request,
)
from tests.snapshot_support import (
    CFG,
    DECLARED,
    EXTRACTOR,
    GOOD,
    TODAY,
    LivePages,
    LiveSearch,
    no_sleep,
    plan_for,
    urls_for,
)

pytestmark = pytest.mark.anyio


def new_store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore.create(tmp_path / "evaluation-snapshot")


async def capture(store, request_id, search, pages, *, retries=2, plan=None):
    return await record_request(
        request_id,
        plan or plan_for(),
        store=store,
        search=search,
        pages=pages,
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=retries,
        today=TODAY,
        sleep=no_sleep,
    )


async def replay(store, plan=None, depth="D2"):
    plan = plan or plan_for()
    return await research(
        plan,
        depth,
        mode=Mode.PRODUCTION,
        search=SnapshotSearch(store, DECLARED),
        pages=SnapshotPages(store),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        today=TODAY,
    )


# ── The store's own refusals ─────────────────────────────────────────────────────────────────


def test_the_store_needs_its_marker_and_will_not_adopt_a_directory(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotAnEvaluationNamespace, match="no .evaluation-namespace"):
        SnapshotStore(plain)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "somebody-elses-file.txt").write_text("x")
    with pytest.raises(NotAnEvaluationNamespace, match="not empty"):
        SnapshotStore.create(busy)
    assert SnapshotStore.create(tmp_path / "ok").root.is_dir()


def test_the_store_refuses_to_live_inside_the_repository():
    inside = REPO_ROOT / "evaluation" / "would-be-store"
    with pytest.raises(NotAnEvaluationNamespace, match="inside the repository"):
        SnapshotStore.create(inside)
    assert not inside.exists()  # refused before anything was made
    with pytest.raises(NotAnEvaluationNamespace, match="inside the repository"):
        SnapshotStore(REPO_ROOT)


# ── Record, then replay ──────────────────────────────────────────────────────────────────────


async def test_a_replay_returns_what_was_recorded_whatever_the_live_page_does_afterwards(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan)
    pages = LivePages()
    report = await capture(store, "R01", LiveSearch(plan, urls), pages)
    assert report.outcome == "committed" and store.committed_requests() == {"R01"}
    first = await replay(store)
    # the live page changes after the capture; the replay must not
    pages.bodies = {u: GOOD.replace("neckband", "EDITED") for us in urls.values() for u in us}
    second = await replay(SnapshotStore(store.root))
    assert [f.page.text for f in first.findings] == [f.page.text for f in second.findings]
    assert all("EDITED" not in f.page.text for f in second.findings) and second.findings
    assert second.searches_issued == 8 and second.pages_fetched == 16


async def test_a_replay_never_reaches_the_network_and_a_miss_is_an_error(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    await capture(store, "R01", LiveSearch(plan, urls_for(plan)), LivePages())
    with pytest.raises(SnapshotMiss, match="query not in the snapshot"):
        await SnapshotSearch(store, DECLARED).search("a query nobody recorded")
    with pytest.raises(SnapshotMiss, match="page not in the snapshot"):
        await SnapshotPages(store).fetch("https://never-recorded.example/p")
    # the engine set is part of the key: a different declared set finds nothing
    with pytest.raises(SnapshotMiss):
        await SnapshotSearch(store, ("yep",)).search(plan.queries[0].text)


async def test_replay_can_serve_any_depth_as_a_prefix_of_the_recorded_list(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    await capture(store, "R01", LiveSearch(plan, urls_for(plan)), LivePages())
    d1, d0 = await replay(store, depth="D1"), await replay(store, depth="D0")
    assert (d1.searches_issued, d1.pages_fetched) == (4, 8)
    assert (d0.searches_issued, d0.pages_fetched) == (0, 0)


# ── Whole or nothing ─────────────────────────────────────────────────────────────────────────


async def test_a_throttled_request_commits_nothing_and_is_captured_again_later(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan)
    throttled = LiveSearch(plan, urls, raise_on={3: SearchRateLimited("too many requests")})
    pages = LivePages()
    report = await capture(store, "R01", throttled, pages)
    assert report.outcome == "aborted" and "throttled" in (report.abort_reason or "")
    assert pages.calls == []  # no page fetched for a request that is to be redone
    assert store.committed_requests() == set() and store.manifest_summary()["searches"] == 0
    assert not (store.root / "staging" / "R01").exists()  # staging discarded
    assert store.pending(["R01", "R02"]) == ["R01", "R02"]
    again = await capture(store, "R01", LiveSearch(plan, urls), LivePages())
    assert again.outcome == "committed" and store.pending(["R01", "R02"]) == ["R02"]


async def test_a_degraded_search_is_never_recorded(tmp_path):
    store, plan = new_store(tmp_path), plan_for()

    class Degraded(LiveSearch):
        async def search(self, query, *, limit=10):
            r = await super().search(query, limit=limit)
            if self.by_text[query] == 2:
                return SearchResponse(r.results, failed_engines=(EngineFailure("yep", "503"),))
            return r

    report = await capture(store, "R01", Degraded(plan, urls_for(plan)), LivePages())
    assert report.outcome == "aborted" and "degraded" in (report.abort_reason or "")
    assert store.manifest_summary()["searches"] == 0


async def test_a_crash_before_the_committed_line_leaves_nothing_visible(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    await capture(store, "R01", LiveSearch(plan, urls_for(plan)), LivePages())
    # a capture that died after appending its entries but before its committed line
    with (store.root / "pages.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"url": "ghost.example/p", "request": "R99", "outcome": "ok"}) + "\n")
    with (store.root / "searches.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": "ghost", "request": "R99", "results": []}) + "\n")
    reopened = SnapshotStore(store.root)
    assert reopened.page_entry("ghost.example/p") is None and reopened.search_entry("ghost") is None
    assert reopened.committed_requests() == {"R01"}


async def test_a_committed_request_cannot_be_captured_again(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    await capture(store, "R01", LiveSearch(plan, urls_for(plan)), LivePages())
    with pytest.raises(SnapshotError, match="write-once"):
        store.begin("R01")


# ── Write once, shared across requests ───────────────────────────────────────────────────────


async def test_a_second_request_sharing_a_url_reads_the_first_requests_bytes(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan)
    await capture(store, "R01", LiveSearch(plan, urls), LivePages())
    # R03 is a repeat campaign: the same queries, the same URLs; the live web has changed since
    changed = LivePages({u: "CHANGED " + GOOD for us in urls.values() for u in us})
    search = LiveSearch(plan, urls)
    report = await capture(store, "R03", search, changed)
    assert changed.calls == [] and search.calls == []  # nothing refetched, nothing re-asked
    assert report.tally.pages_reused == 16 and report.tally.searches_reused == 8
    after = await replay(SnapshotStore(store.root))
    assert all(not f.page.text.startswith("CHANGED") for f in after.findings)


# ── Failures are frozen, with a bound ────────────────────────────────────────────────────────


async def test_a_permanent_failure_is_frozen_as_an_outcome_and_replays_as_the_same_refusal(
    tmp_path,
):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan, per_query=1)
    gone, robots, pdf = urls[1][0], urls[2][0], urls[3][0]
    pages = LivePages(
        failing={
            gone: FetchFailed("404", status=404, kind="http_status"),
            robots: FetchRefused("robots disallows", kind="robots"),
            pdf: FetchUnsupported("a PDF", kind="pdf"),
        }
    )
    report = await capture(store, "R01", LiveSearch(plan, urls), pages)
    assert pages.calls.count(gone) == 1  # permanent: not retried
    assert report.tally.frozen_failed_permanent == 3 and report.tally.frozen_failed_transient == 0
    replayed = SnapshotPages(store)
    with pytest.raises(FetchFailed) as e404:
        await replayed.fetch(gone)
    assert e404.value.status == 404 and e404.value.kind == "http_status"
    with pytest.raises(FetchRefused):
        await replayed.fetch(robots)
    with pytest.raises(FetchUnsupported):
        await replayed.fetch(pdf)
    entry = store.page_entry("r1-0.example/p")
    assert entry and entry["outcome"] == "failed" and entry["attempts"] == 1


async def test_a_transient_failure_is_retried_a_bounded_number_of_times_then_frozen(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan, per_query=1)
    flaky = urls[1][0]
    timeout = FetchFailed("timed out", kind="timeout")
    pages = LivePages(failing={flaky: [timeout] * 10})
    report = await capture(store, "R01", LiveSearch(plan, urls), pages, retries=2)
    assert pages.calls.count(flaky) == 3  # one attempt and exactly two retries: the bound
    assert report.tally.retries_used == 2 and report.tally.frozen_failed_transient == 1
    entry = store.page_entry("r1-0.example/p")
    assert (
        entry["attempts"] == 3
        and entry["transient"] is True
        and entry["error"]["kind"] == "timeout"
    )
    with pytest.raises(FetchFailed, match="timed out"):
        await SnapshotPages(store).fetch(flaky)


async def test_a_transient_failure_that_clears_on_a_retry_is_stored_as_the_page(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan, per_query=1)
    url = urls[1][0]
    pages = LivePages(failing={url: [FetchFailed("503", status=503, kind="http_status")]})
    report = await capture(store, "R01", LiveSearch(plan, urls), pages, retries=2)
    entry = store.page_entry("r1-0.example/p")
    assert entry["outcome"] == "ok" and entry["attempts"] == 2
    assert report.tally.retries_used == 1 and report.frozen_failed == 0
    assert (await SnapshotPages(store).fetch(url)).html == GOOD


async def test_the_retry_bound_is_explicit_zero_means_one_attempt_and_negative_is_refused(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan, per_query=1)
    url = urls[1][0]
    pages = LivePages(failing={url: [FetchFailed("t", kind="timeout")] * 5})
    await capture(store, "R01", LiveSearch(plan, urls), pages, retries=0)
    assert pages.calls.count(url) == 1
    with pytest.raises(ValueError, match="bound is explicit"):
        await capture(store, "R02", LiveSearch(plan, urls), LivePages(), retries=-1)


async def test_the_frozen_failed_share_is_reported_per_request_and_lands_in_the_manifest(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan, per_query=1)
    pages = LivePages(
        failing={
            urls[1][0]: FetchFailed("404", status=404, kind="http_status"),
            urls[2][0]: [FetchFailed("t", kind="timeout")] * 9,
        }
    )
    report = await capture(store, "R01", LiveSearch(plan, urls), pages, retries=2)
    assert report.pages_total == 8 and report.frozen_failed == 2
    assert report.frozen_failed_share == 0.25
    line = store.committed["R01"]
    assert line["frozen_failed"] == 2 and line["pages"] == 8 and line["retries_bound"] == 2
    summary = store.manifest_summary()
    assert summary["pages_frozen_failed"] == 2 and summary["pages_ok"] == 6


# ── What the repository may hold, and the costs ──────────────────────────────────────────────


async def test_the_manifest_summary_holds_counts_and_a_hash_and_never_a_body(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    await capture(store, "R01", LiveSearch(plan, urls_for(plan)), LivePages())
    summary = store.manifest_summary()
    assert summary["requests"] == 1 and summary["searches"] == 8 and summary["pages"] == 16
    assert summary["first_committed_utc"] and len(summary["sha256"]) == 64
    assert GOOD[:30] not in json.dumps(summary)  # counts and a hash, not a body
    assert SnapshotStore(store.root).manifest_summary()["sha256"] == summary["sha256"]


async def test_the_recording_carries_the_unit_costs_the_derived_web_time_needs(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    report = await capture(store, "R01", LiveSearch(plan, urls_for(plan)), LivePages())
    assert len(report.tally.search_seconds) == 8 and len(report.tally.page_seconds) == 16
    assert all("elapsed_s" in e for e in store._searches.values())
    assert all("elapsed_s" in e for e in store._pages.values())


# ── Session-scoped reuse of clean searches, and the span it puts on the record ───────────────


async def test_an_aborted_attempt_hands_back_its_clean_searches_and_the_next_attempt_reuses_them(
    tmp_path,
):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan)
    first = await capture(
        store,
        "R01",
        LiveSearch(plan, urls, raise_on={4: SearchRateLimited("throttled")}),
        LivePages(),
    )
    assert first.outcome == "aborted" and len(first.carry) == 3  # queries 1 to 3 were clean
    assert store.committed_requests() == set()  # nothing committed, only handed back
    retry = LiveSearch(plan, urls)
    second = await record_request(
        "R01",
        plan,
        store=store,
        search=retry,
        pages=LivePages(),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=2,
        today=TODAY,
        sleep=no_sleep,
        carry=first.carry,
    )
    assert second.outcome == "committed" and second.tally.searches_carried == 3
    assert retry.calls == [4, 5, 6, 7, 8]  # only the five not yet captured went live


async def test_the_committed_line_carries_the_span_including_a_carried_searchs_original_time(
    tmp_path,
):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan)
    first = await capture(
        store,
        "R01",
        LiveSearch(plan, urls, raise_on={3: SearchRateLimited("throttled")}),
        LivePages(),
    )
    # the carried searches were fetched hours ago, as in a long session with many refusals
    old = "2026-10-28T06:00:00+00:00"
    carry = {k: {**v, "fetched_utc": old} for k, v in first.carry.items()}
    report = await record_request(
        "R01",
        plan,
        store=store,
        search=LiveSearch(plan, urls),
        pages=LivePages(),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=2,
        today=TODAY,
        sleep=no_sleep,
        carry=carry,
    )
    line = store.committed["R01"]
    # the old searches were fetched at 06:00 UTC today; the new ones just now: a span of hours
    assert report.span_s is not None and 3600 < report.span_s < 24 * 3600
    assert line["search_span_s"] == report.span_s and line["first_search_utc"] == old
    assert line["searches_carried"] == 2
    assert store.manifest_summary()["search_span_s"]["max"] == report.span_s


async def test_a_search_reused_from_another_request_counts_in_the_wider_span_only(tmp_path):
    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan)
    await capture(store, "R01", LiveSearch(plan, urls), LivePages())
    # make R01's recorded searches look old, as if they came from a session days earlier
    for entry in store._searches.values():
        entry["fetched_utc"] = "2026-10-20T00:00:00+00:00"
    report = await capture(store, "R03", LiveSearch(plan, urls), LivePages())  # all eight reused
    assert report.tally.searches_reused == 8 and report.tally.searches_new == 0
    assert report.span_s is None  # nothing was captured FOR this request
    assert report.span_all_s == 0.0  # eight answers from one instant


# ── The unit costs exclude waiting (found by the first live capture) ─────────────────────────


async def test_a_searchs_recorded_seconds_exclude_the_pacing_wait_before_it(tmp_path):
    import asyncio

    class SlowPace:
        async def wait(self) -> None:
            await asyncio.sleep(0.3)  # the gap between live searches

        def clean(self) -> None: ...

        def refused(self) -> None: ...

    store, plan = new_store(tmp_path), plan_for()
    await record_request(
        "R01",
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
        pace=SlowPace(),
    )
    seconds = [e["elapsed_s"] for e in store._searches.values()]
    assert (
        len(seconds) == 8 and max(seconds) < 0.15
    )  # the answer took about nothing; the gap was 0.3


async def test_a_pages_recorded_seconds_exclude_the_retry_backoff(tmp_path):
    import asyncio

    async def real_sleep(_seconds: float) -> None:
        await asyncio.sleep(0.3)

    store, plan = new_store(tmp_path), plan_for()
    urls = urls_for(plan, per_query=1)
    flaky = urls[1][0]
    pages = LivePages(failing={flaky: [FetchFailed("503", status=503, kind="http_status")]})
    await record_request(
        "R01",
        plan,
        store=store,
        search=LiveSearch(plan, urls),
        pages=pages,
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        retries=2,
        today=TODAY,
        sleep=real_sleep,
    )
    entry = store.page_entry("r1-0.example/p")
    assert entry["attempts"] == 2 and entry["elapsed_s"] < 0.15  # two quick attempts, 0.3 s backoff
