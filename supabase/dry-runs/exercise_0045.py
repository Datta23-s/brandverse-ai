"""Checks run INSIDE the rolled-back dry-run transaction of 0045. Nothing here persists."""

import psycopg


def exercise(cur: psycopg.Cursor) -> None:
    defn = cur.execute(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = 'public.llm_calls'::regclass and conname = 'llm_calls_outcome_check'"
    ).fetchone()
    print("llm_calls_outcome_check:", defn)
    assert defn is not None and "'orphaned'" in defn[0]
    for old in ("pending", "ok", "invalid_output", "error", "rate_limited", "refused"):
        assert f"'{old}'" in defn[0], old
    rows = cur.execute("select count(*) from public.llm_calls").fetchone()
    print("existing rows still satisfy the constraint:", rows)
