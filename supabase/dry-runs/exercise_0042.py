"""Checks run INSIDE the rolled-back dry-run transaction of 0041 and 0042 (the harness calls
`exercise(cursor)` after the files are applied). Nothing here persists."""

import psycopg


def exercise(cur: psycopg.Cursor) -> None:
    before = cur.execute("select count(*) from public.audit_log").fetchone()
    assert before is not None
    for i in range(3):
        cur.execute(
            "insert into public.audit_log (actor_type, actor_id, action, entity_type, entity_id) "
            "values ('service', 'dry-run', 'chain.dry_run', 'test', %s)",
            (str(i),),
        )
    row = cur.execute("select * from app.chain_verify('audit_log')").fetchone()
    assert row is not None
    chained, unchained, head_seq, head_hash, bad, ok = row
    print(f"audit_log: chained={chained} unchained={unchained} head_seq={head_seq} ok={ok} bad={bad}")
    assert chained == 3 and unchained == before[0] and ok and head_seq == 3 and len(head_hash) == 64
    cur.execute("update public.audit_log set action = 'x' where false")
    row = cur.execute("select * from app.chain_verify('approvals')").fetchone()
    assert row is not None and row[5] is True
    for role in ("anon", "authenticated", "service_role"):
        got = cur.execute(
            "select has_function_privilege(%s, 'app.chain_verify(text)', 'EXECUTE')", (role,)
        ).fetchone()
        assert got is not None
        print(f"chain_verify executable by {role}: {got[0]}")
        assert got[0] == (role == "service_role")
    cols = cur.execute(
        "select count(*) from information_schema.columns where table_schema = 'public' and "
        "table_name in ('approvals', 'campaigns') and column_name = 'revision_round'"
    ).fetchone()
    assert cols is not None and cols[0] == 2
    print("revision_round on approvals and campaigns: present")
