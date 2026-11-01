"""Checks run INSIDE the rolled-back dry-run transaction of 0044 (the harness calls
`exercise(cursor)` after the file is applied). Nothing here persists."""

import psycopg


def exercise(cur: psycopg.Cursor) -> None:
    col = cur.execute(
        "select data_type, is_nullable from information_schema.columns "
        "where table_schema = 'public' and table_name = 'runs' and column_name = 'timings'"
    ).fetchone()
    print("runs.timings:", col)
    assert col == ("jsonb", "YES")
    body = cur.execute(
        "select pg_get_functiondef('app.runs_initial_state'::regproc)"
    ).fetchone()
    assert body is not None and "new.timings := null" in body[0]
    print("runs_initial_state forces timings to null for an API role: present")
    # The check refuses anything but a JSON object (an array is not a set of named figures).
    cur.execute("savepoint s")
    try:
        cur.execute("update public.runs set timings = '[1]'::jsonb where false")
        refused = cur.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.runs'::regclass and conname like 'runs_timings_check'"
        ).fetchone()
    finally:
        cur.execute("rollback to savepoint s")
    print("check constraint:", refused)
    assert refused is not None and "object" in refused[0]
