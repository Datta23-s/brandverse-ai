"""Test organisations are excluded from evaluation by a flag, never by remembered ids (0020).

The live project's fixed E2E organisations, and everything in them — runs, LLM calls, approvals
and the lasting run fixture (brand, product, verified claim) — can never be deleted by application
roles (0010, 0017). They stay out of every evaluation read because `organisations.is_test` is set
and every read filters on it, even when a test organisation's id is passed in by mistake.
"""

import importlib
import json
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from app.evaluation.approvals import EVALUATION_APPROVALS_SQL, PRACTICE_READS
from app.evaluation.prompt_versions import prompt_versions_by_arm
from app.evaluation.run_outcomes import run_outcomes_by_arm
from tests.db.conftest import Seed, Tx

pytestmark = pytest.mark.db

EVALUATION_DIR = Path(__file__).resolve().parents[2] / "app" / "evaluation"


def _one(c: psycopg.Connection, query: str, params: tuple[object, ...]) -> UUID:
    row = c.execute(query, params).fetchone()
    assert row is not None
    value: UUID = row[0]
    return value


def _test_org_with_everything(db_url: str, seed: Seed) -> UUID:
    """A flagged test org holding a fixture brand/product/verified claim, a blocked and a
    completed run, an LLM call, a campaign and an approval: every kind of row a read could pick up.
    """
    with psycopg.connect(db_url, autocommit=True) as c:
        org = _one(
            c, "insert into organisations (name, is_test) values ('E2E Org', true) returning id", ()
        )
        owner, approver = seed.users["outsider"], seed.users["second_scorer"]
        c.execute(
            "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner'), "
            "(%s, %s, 'approver')",
            (org, owner, org, approver),
        )
        brand = _one(
            c,
            "insert into brands (org_id, name, sector) values (%s, 'E2E run fixture', 'footwear') "
            "returning id",
            (org,),
        )
        product = _one(
            c,
            "insert into products (org_id, brand_id, name) values (%s, %s, 'E2E fixture shoe') "
            "returning id",
            (org, brand),
        )
        with c.transaction():
            c.execute(
                "select set_config('role', 'authenticated', true), "
                "set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(owner), "role": "authenticated"}),),
            )
            c.execute(
                "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
                "created_by) values (%s, %s, 'fixture', 'verified', 'https://example.invalid/f', "
                "'human')",
                (org, product),
            )
        run = _one(
            c,
            "insert into runs (org_id, product_id, arm, request, status) "
            "values (%s, %s, 'proposed', '{}', 'blocked') returning id",
            (org, product),
        )
        c.execute(
            "insert into runs (org_id, product_id, arm, request, status) "
            "values (%s, %s, 'proposed', '{}', 'completed')",
            (org, product),
        )
        c.execute(
            "insert into llm_calls (org_id, run_id, model, tier, prompt_id, prompt_version, "
            "route_reason, outcome) values (%s, %s, 'm', 'cheap', 'brief', 'v9', 'test', 'ok')",
            (org, run),
        )
        campaign = _one(
            c, "insert into campaigns (org_id, run_id) values (%s, %s) returning id", (org, run)
        )
        c.execute(
            "insert into approvals (campaign_id, org_id, approver_id, outcome, seconds_spent, "
            "reason) values (%s, %s, %s, 'reject', 30, 'test org')",
            (campaign, org, approver),
        )
    return org


@pytest.mark.anyio
async def test_a_test_orgs_rows_never_reach_any_evaluation_read(db_url: str, seed: Seed) -> None:
    test_org = _test_org_with_everything(db_url, seed)
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        # Its id is passed in, as by a mistake: the flag still keeps it out.
        outcomes = await run_outcomes_by_arm(conn, [test_org])
        prompts = await prompt_versions_by_arm(conn, [test_org])
        cur = await conn.execute(EVALUATION_APPROVALS_SQL, {"org_ids": [test_org]})
        approvals = await cur.fetchall()
    assert all(a.total == 0 for a in outcomes)
    assert prompts.uses == []
    assert approvals == []


@pytest.mark.anyio
async def test_the_same_rows_are_counted_once_the_flag_is_off(db_url: str, seed: Seed) -> None:
    # Control: the exclusion is the flag, not something else about these rows.
    test_org = _test_org_with_everything(db_url, seed)
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("update organisations set is_test = false where id = %s", (test_org,))
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        outcomes = {a.arm: a for a in await run_outcomes_by_arm(conn, [test_org])}
        prompts = await prompt_versions_by_arm(conn, [test_org])
        cur = await conn.execute(EVALUATION_APPROVALS_SQL, {"org_ids": [test_org]})
        approvals = await cur.fetchall()
    assert outcomes["proposed"].blocked == 1 and outcomes["proposed"].total == 2
    assert [u.prompt_id for u in prompts.uses] == ["brief"]
    assert len(approvals) == 1


def _sql_strings() -> dict[str, str]:
    """Every module-level *_SQL string in app/evaluation, derived ones included (the module is
    imported, not parsed, so `X_SQL = Y_SQL.replace(...)` is seen too)."""
    found: dict[str, str] = {}
    for path in sorted(EVALUATION_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = importlib.import_module(f"app.evaluation.{path.stem}")
        for name, value in vars(module).items():
            if (
                name.endswith("_SQL")
                and isinstance(value, str)
                and value.lstrip().startswith(("select", "with"))
            ):
                found[f"{path.name}:{name}"] = value
    return found


def test_every_evaluation_read_excludes_practice_and_test_organisations() -> None:
    reads = _sql_strings()
    assert len(reads) >= 3, f"evaluation reads not found: {sorted(reads)}"
    practice = {n for n in reads if n.split(":")[1] in PRACTICE_READS}
    assert {n.split(":")[1] for n in practice} == PRACTICE_READS, "a declared practice read is gone"
    for name, query in reads.items():
        assert "not o.is_test" in query, f"{name} does not exclude test organisations"
        if name in practice:
            continue
        assert "not o.is_practice" in query, f"{name} does not exclude practice organisations"


def test_the_reads_that_include_practice_organisations_are_declared_and_read_one_kind() -> None:
    """The exemption is not a gap: a practice read says `o.is_practice`, reads exactly one of
    calibration or tuning, never evaluation, and an evaluation read cannot be on the list."""
    reads = _sql_strings()
    for name, query in reads.items():
        short = name.split(":")[1]
        if short in PRACTICE_READS:
            assert "and o.is_practice" in query and "not o.is_practice" not in query, name
            assert ("a.kind = 'calibration'" in query) != ("a.kind = 'tuning'" in query), name
            assert "'evaluation'" not in query, name
        else:
            assert "o.is_practice" not in query.replace("not o.is_practice", ""), (
                f"{name} reads practice organisations but is not declared in PRACTICE_READS"
            )


# ── Who may set the flag ─────────────────────────────────────────────────────────────────────


def test_an_owner_cannot_mark_or_unmark_their_organisation_as_a_test(tx: Tx) -> None:
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute("update organisations set is_test = true where id = %s", (tx.seed.org_a,))


def test_signed_in_users_hold_no_update_on_is_test(tx: Tx) -> None:
    rows = tx.execute(
        "select has_column_privilege('authenticated', 'public.organisations', 'is_test', 'UPDATE')"
    )
    assert rows == [(False,)]


def test_anon_has_no_update_policy_on_organisations(tx: Tx) -> None:
    # anon keeps Supabase's default table grant (0009 narrowed authenticated only), but no policy
    # admits it, so row-level security refuses every update.
    rows = tx.execute(
        "select policyname from pg_policies where tablename = 'organisations' "
        "and cmd in ('UPDATE', 'ALL') and ('anon' = any (roles) or 'public' = any (roles))"
    )
    assert rows == []
    tx.execute("set local role anon")
    assert tx.execute("update organisations set is_test = true returning id") == []


def test_the_bootstrap_cannot_create_a_test_organisation(tx: Tx) -> None:
    rows = tx.execute(
        "select with_check from pg_policies where tablename = 'organisations' "
        "and policyname = 'org_creator_insert'"
    )
    assert rows == [("((NOT is_practice) AND (NOT is_test))",)]
    tx.as_user("outsider")
    org = tx.execute("select app.create_organisation('Fresh')")[0][0]
    tx.execute("reset role")
    assert tx.execute("select is_test from organisations where id = %s", (org,)) == [(False,)]
