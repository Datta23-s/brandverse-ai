"""Checks run INSIDE the rolled-back dry-run transaction of 0048. Nothing here persists."""

import psycopg


def exercise(cur: psycopg.Cursor) -> None:
    for column, kind in (
        ("pages_fetched", "integer"),
        ("pages_served", "integer"),
        ("facts", "jsonb"),
        ("supersedes_run_id", "uuid"),
    ):
        col = cur.execute(
            "select data_type, is_nullable from information_schema.columns "
            "where table_schema = 'public' and table_name = 'runs' and column_name = %s",
            (column,),
        ).fetchone()
        print(f"runs.{column}:", col)
        assert col == (kind, "YES")

    fn = cur.execute("select pg_get_functiondef('app.runs_initial_state'::regproc)").fetchone()
    assert fn is not None
    body = fn[0]
    for forced in (
        "new.pages_fetched := null",
        "new.pages_served := null",
        "new.facts := null",
        "new.supersedes_run_id := null",
        # everything forced before 0048 is still forced
        "new.failure := null",
        "new.timings := null",
        "new.brief_check := null",
        "new.declared_gaps",
    ):
        assert forced in body, forced
    print("app.runs_initial_state forces the four new columns and keeps the earlier ones")

    idx = cur.execute(
        "select indexdef from pg_indexes where schemaname = 'public' "
        "and indexname = 'runs_superseded_once'"
    ).fetchone()
    print("runs_superseded_once:", idx)
    assert idx is not None and "UNIQUE" in idx[0]

    n = cur.execute(
        "select count(*) from public.runs where supersedes_run_id is not null "
        "or pages_fetched is not null or facts is not null"
    ).fetchone()
    print("existing runs touched by the new columns (must be 0):", n)
    assert n == (0,)
