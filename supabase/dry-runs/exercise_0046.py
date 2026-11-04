"""Checks run INSIDE the rolled-back dry-run transaction of 0046. Nothing here persists."""

import psycopg


def exercise(cur: psycopg.Cursor) -> None:
    col = cur.execute(
        "select data_type, is_nullable from information_schema.columns "
        "where table_schema = 'public' and table_name = 'runs' and column_name = 'failure'"
    ).fetchone()
    print("runs.failure:", col)
    assert col == ("jsonb", "YES")

    check = cur.execute(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = 'public.runs'::regclass and contype = 'c' "
        "and pg_get_constraintdef(oid) like '%failure%'"
    ).fetchone()
    print("check on failure:", check)
    assert check is not None and "jsonb_typeof" in check[0] and "'object'" in check[0]

    fn = cur.execute("select pg_get_functiondef('app.runs_initial_state'::regproc)").fetchone()
    assert fn is not None
    body = fn[0]
    assert "new.failure := null" in body, "the API-role insert must force failure to null"
    # Everything the function forced before 0046 is still forced.
    for forced in ("new.timings := null", "new.brief_check := null", "new.declared_gaps"):
        assert forced in body, forced
    print("app.runs_initial_state forces failure null and keeps the earlier columns forced")

    n = cur.execute("select count(*) from public.runs where failure is not null").fetchone()
    print("existing runs with a failure recorded (must be 0):", n)
    assert n == (0,)
