"""A prompt changed mid-evaluation is DETECTABLE from llm_calls (Rules.md A5, A6; migration 0014).

Each test works in a fresh organisation of its own, so rows other tests write never enter the
report.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from app.evaluation.prompt_versions import PromptVersionReport, prompt_versions_by_arm
from tests.db.conftest import Seed

pytestmark = [pytest.mark.anyio, pytest.mark.db]


class Evaluation:
    """Builds runs in arms and the llm_calls rows they made."""

    def __init__(self, db_url: str, org_id: UUID | None = None) -> None:
        self.db_url = db_url
        with psycopg.connect(db_url, autocommit=True) as c:
            if org_id is None:
                row = c.execute(
                    "insert into organisations (name) values ('prompt-version test') returning id"
                ).fetchone()
                assert row is not None
                org_id = row[0]
        assert org_id is not None
        self.org_id: UUID = org_id

    def run(self, arm: str, *calls: str, outcome: str = "ok", age: str = "0 seconds") -> None:
        """One run in `arm`, making one call per 'prompt@version' given."""
        with psycopg.connect(self.db_url, autocommit=True) as c:
            row = c.execute(
                "insert into runs (org_id, arm, request) values (%s, %s, '{}') returning id",
                (self.org_id, arm),
            ).fetchone()
            assert row is not None
            for call in calls:
                prompt_id, version = call.split("@")
                c.execute(
                    "insert into llm_calls (org_id, run_id, model, tier, prompt_id, "
                    "prompt_version, route_reason, outcome, created_at) values (%s, %s, 'm', "
                    "'cheap', %s, %s, 'test', %s, now() - %s::interval)",
                    (self.org_id, row[0], prompt_id, version, outcome, age),
                )

    async def report(self, *extra_orgs: UUID, **window: datetime) -> PromptVersionReport:
        async with await psycopg.AsyncConnection.connect(self.db_url) as conn:
            return await prompt_versions_by_arm(conn, [self.org_id, *extra_orgs], **window)


async def test_one_version_per_prompt_in_every_arm_is_consistent(db_url: str) -> None:
    ev = Evaluation(db_url)
    for _ in range(3):
        ev.run("proposed", "brief@v1", "memory_rank@v2", "brief@v1")
        ev.run("baseline_a", "brief@v1")
        ev.run("baseline_b", "brief@v1")
    report = await ev.report()
    # The baselines never use memory_rank: absent, which is not a difference.
    assert report.consistent, report
    by = {(u.arm, u.prompt_id): (u.prompt_version, u.calls, u.runs) for u in report.uses}
    assert by[("proposed", "brief")] == ("v1", 6, 3)
    assert by[("proposed", "memory_rank")] == ("v2", 3, 3)
    assert ("baseline_a", "memory_rank") not in by


async def test_a_prompt_changed_mid_arm_is_flagged(db_url: str) -> None:
    ev = Evaluation(db_url)
    ev.run("proposed", "brief@v1")
    ev.run("proposed", "brief@v1")
    ev.run("proposed", "brief@v2")  # edited after run 2
    ev.run("baseline_a", "brief@v1")
    report = await ev.report()
    assert not report.consistent
    assert report.mixed_within_arm == {("proposed", "brief"): ["v1", "v2"]}
    assert report.differs_across_arms == {"brief": {"baseline_a": ["v1"], "proposed": ["v1", "v2"]}}


async def test_arms_on_different_versions_are_flagged_even_if_each_is_uniform(
    db_url: str,
) -> None:
    ev = Evaluation(db_url)
    ev.run("proposed", "brief@v2")
    ev.run("baseline_b", "brief@v1")
    report = await ev.report()
    assert report.mixed_within_arm == {}
    assert report.differs_across_arms == {"brief": {"baseline_b": ["v1"], "proposed": ["v2"]}}


async def test_refused_calls_count_they_show_what_the_code_would_have_sent(db_url: str) -> None:
    ev = Evaluation(db_url)
    ev.run("proposed", "brief@v1")
    ev.run("proposed", "brief@v2", outcome="refused")
    assert (await ev.report()).mixed_within_arm == {("proposed", "brief"): ["v1", "v2"]}


async def test_practice_organisations_and_other_organisations_are_excluded(
    db_url: str, seed: Seed
) -> None:
    ev = Evaluation(db_url)
    ev.run("proposed", "brief@v1")
    Evaluation(db_url, seed.org_practice).run("proposed", "brief@v9")  # calibration practice
    Evaluation(db_url).run("proposed", "brief@v8")  # not an evaluation organisation
    report = await ev.report(seed.org_practice)
    assert report.consistent
    assert {u.prompt_version for u in report.uses} == {"v1"}


async def test_the_window_bounds_the_evaluation_period(db_url: str) -> None:
    ev = Evaluation(db_url)
    ev.run("proposed", "brief@v1", age="2 days")  # development, before
    ev.run("proposed", "brief@v2")
    since = datetime.now(UTC) - timedelta(hours=1)
    assert (await ev.report(since=since)).consistent
    assert not (await ev.report()).consistent
    assert (await ev.report(until=since)).consistent
