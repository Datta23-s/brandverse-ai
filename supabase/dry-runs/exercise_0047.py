"""Checks run INSIDE the rolled-back dry-run transaction of 0047. Nothing here persists."""

import psycopg

OLD_OUTCOMES = ("pending", "ok", "invalid_output", "error", "rate_limited", "refused", "orphaned")


def exercise(cur: psycopg.Cursor) -> None:
    defn = cur.execute(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = 'public.llm_calls'::regclass and conname = 'llm_calls_outcome_check'"
    ).fetchone()
    print("llm_calls_outcome_check:", defn)
    assert defn is not None and "'provider_unavailable'" in defn[0]
    for old in OLD_OUTCOMES:
        assert f"'{old}'" in defn[0], old  # every earlier outcome is still allowed
    rows = cur.execute("select count(*) from public.llm_calls").fetchone()
    print("existing rows still satisfy the constraint:", rows)
    bad = cur.execute(
        "select count(*) from public.llm_calls "
        "where outcome not in ('pending','ok','invalid_output','error','rate_limited',"
        "'refused','orphaned','provider_unavailable')"
    ).fetchone()
    assert bad == (0,)
