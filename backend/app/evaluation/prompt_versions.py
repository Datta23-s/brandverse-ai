"""Did every evaluation run in an arm use the same prompts? (Rules.md A5, A6.)

A prompt edited mid-evaluation would silently invalidate the comparison between arms. Every
llm_calls row records the template that produced it (prompt_id, prompt_version; migration 0014),
so a change is DETECTABLE from the raw log, not merely recorded: this report lists, per prompt,
the versions each arm used, and flags

- `mixed_within_arm`: one arm used more than one version of a prompt — runs inside the arm are
  not comparable with each other;
- `differs_across_arms`: arms that both used a prompt used different versions of it — the arms
  are not comparable on that prompt. An arm that never used a prompt (e.g. baselines have no
  memory step) is not a difference.

Every row counts, refusals included: a refused call still shows which prompt the code would have
sent, so a version change is caught even in a run that was refused before it reached the model.

Scoped to the evaluation organisations passed in (Rules.md P3), excluding practice and test
organisations (is_practice, is_test: flags, never ids), as the approvals read is
(app/evaluation/approvals.py). `since` / `until` bound the evaluation period by call time.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection

PROMPT_VERSIONS_BY_ARM_SQL = """
select r.arm, c.prompt_id, c.prompt_version,
       count(*)::int, count(distinct c.run_id)::int, min(c.created_at), max(c.created_at)
from public.llm_calls c
join public.runs r on r.id = c.run_id and r.org_id = c.org_id
join public.organisations o on o.id = c.org_id
where c.org_id = any(%(org_ids)s)
  and not o.is_practice
  and not o.is_test
  and (%(since)s::timestamptz is null or c.created_at >= %(since)s::timestamptz)
  and (%(until)s::timestamptz is null or c.created_at < %(until)s::timestamptz)
group by r.arm, c.prompt_id, c.prompt_version
order by c.prompt_id, r.arm, c.prompt_version
"""


@dataclass(frozen=True, slots=True)
class VersionUse:
    arm: str
    prompt_id: str
    prompt_version: str
    calls: int
    runs: int
    first_at: datetime
    last_at: datetime


@dataclass(frozen=True, slots=True)
class PromptVersionReport:
    uses: list[VersionUse]
    # (arm, prompt_id) -> versions, where an arm used more than one.
    mixed_within_arm: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # prompt_id -> {arm: versions}, where the arms using it did not all use the same versions.
    differs_across_arms: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @property
    def consistent(self) -> bool:
        return not self.mixed_within_arm and not self.differs_across_arms


def summarise(uses: list[VersionUse]) -> PromptVersionReport:
    by_prompt: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for u in uses:
        by_prompt[u.prompt_id][u.arm].add(u.prompt_version)
    mixed = {
        (arm, prompt_id): sorted(versions)
        for prompt_id, arms in by_prompt.items()
        for arm, versions in arms.items()
        if len(versions) > 1
    }
    differs = {
        prompt_id: {arm: sorted(v) for arm, v in sorted(arms.items())}
        for prompt_id, arms in by_prompt.items()
        if len({frozenset(v) for v in arms.values()}) > 1
    }
    return PromptVersionReport(uses, mixed, differs)


async def prompt_versions_by_arm(
    conn: AsyncConnection,
    org_ids: list[UUID],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> PromptVersionReport:
    cur = await conn.execute(
        PROMPT_VERSIONS_BY_ARM_SQL, {"org_ids": org_ids, "since": since, "until": until}
    )
    return summarise([VersionUse(*row) for row in await cur.fetchall()])
