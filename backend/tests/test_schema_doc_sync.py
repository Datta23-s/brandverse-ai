"""Architecture.md §9 lists every migration file, and copies none of them (Rules.md §8).

Until 2026-10-18 §9 held a copy of the migration SQL, kept in step by a script; a doc that
duplicates code drifts, and the script needed an exception to the no-edit-scripts rule (Rules.md
§3.3). Now §9 holds a table of file paths and the commits that added them, so there is nothing to
sync, and these tests check only that the table is complete and that no copy creeps back. The
other tests here read the migrations themselves.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")
# | `supabase/migrations/NNNN_name.sql` | `added` | `changed` or empty | what it does |
_MIGRATION_ROW = re.compile(
    r"^\| `supabase/migrations/(\d{4}_[a-z0-9_]+\.sql)` "
    r"\| `[0-9a-f]{7,40}` \| *(?:`[0-9a-f]{7,40}`)? *\|",
    re.M,
)


def split_statements(sql: str) -> list[str]:
    """Split SQL into normalised statements; aware of comments, quotes and dollar quoting."""
    out, buf, i = [], [], 0
    while i < len(sql):
        if sql.startswith("--", i):
            i = sql.find("\n", i) if "\n" in sql[i:] else len(sql)
            buf.append(" ")
            continue
        ch = sql[i]
        if ch == "'":
            end = i + 1
            while end < len(sql):
                if sql[end] == "'" and sql[end : end + 2] != "''":
                    break
                end += 2 if sql[end : end + 2] == "''" else 1
            buf.append(sql[i : end + 1])
            i = end + 1
            continue
        tag = _DOLLAR_TAG.match(sql, i)
        if tag:
            close = sql.find(tag.group(0), tag.end())
            assert close != -1, f"unterminated dollar quote at {i}"
            buf.append(sql[i : close + len(tag.group(0))])
            i = close + len(tag.group(0))
            continue
        if ch == ";":
            stmt = " ".join("".join(buf).split())
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    assert not " ".join("".join(buf).split()), "trailing SQL without ';'"
    return out


def _section_9() -> str:
    text = (REPO_ROOT / "Architecture.md").read_text(encoding="utf-8")
    return text[text.index("## 9. Data model") : text.index("## 10. API surface")]


def _migrations_sql() -> str:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_architecture_section_9_lists_every_migration_once_in_order() -> None:
    listed = _MIGRATION_ROW.findall(_section_9())
    files = [f.name for f in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert listed == files, f"§9 lists {listed}, the directory holds {files}"


def test_no_migration_file_carries_a_byte_order_mark() -> None:
    """A byte-order mark in a migration breaks it outright: Postgres reads the leading bytes as
    part of the first statement, so the file fails to apply. That is the worst failure shape —
    on 2026-10-26 a BOM written by `Set-Content -Encoding utf8` (PowerShell 5.1 adds one) made the
    database suite ERROR while building its schema, which reads as a broken test run rather than a
    broken file, and a `db push` would have failed the same way against the live project. The
    characters are invisible in an editor and survive a diff, so this scan reads bytes: a BOM at the
    start of a file, and a zero-width no-break space anywhere in it (Rules.md §3.3's hazard, made
    impossible rather than remembered). Every file in the directory is scanned, not only `*.sql`.
    """
    # Every BOM the Unicode standard defines, as bytes, so a file in the wrong encoding entirely
    # is named as such rather than raising while being decoded.
    marks = {
        "UTF-8": b"\xef\xbb\xbf",
        "UTF-32 little-endian": b"\xff\xfe\x00\x00",
        "UTF-32 big-endian": b"\x00\x00\xfe\xff",
        "UTF-16 little-endian": b"\xff\xfe",
        "UTF-16 big-endian": b"\xfe\xff",
    }
    files = sorted(p for p in MIGRATIONS_DIR.rglob("*") if p.is_file())
    assert files, f"no files found in {MIGRATIONS_DIR}"
    offenders = []
    for path in files:
        raw = path.read_bytes()
        name = path.relative_to(REPO_ROOT).as_posix()
        for encoding, mark in marks.items():  # UTF-32 LE before UTF-16 LE: it starts with one
            if raw.startswith(mark):
                offenders.append(f"{name}: starts with a {encoding} byte-order mark")
                break
        else:
            if b"\xef\xbb\xbf" in raw:
                at = raw.index(b"\xef\xbb\xbf")
                offenders.append(f"{name}: zero-width no-break space at byte {at}")
    assert not offenders, "byte-order marks in migration files:\n  " + "\n  ".join(offenders)


def test_architecture_section_9_holds_no_copy_of_the_sql() -> None:
    assert "```sql" not in _section_9(), "§9 references the migration files; it does not copy them"


def test_splitter_handles_quotes_and_dollar_bodies() -> None:
    sql = "select 'a;b' ; create function f() returns int as $$ select 1; $$ language sql; -- x;\n"
    assert split_statements(sql) == [
        "select 'a;b'",
        "create function f() returns int as $$ select 1; $$ language sql",
    ]


def test_migrations_read_no_org_claim() -> None:
    # The JWT carries no organisation: membership is resolved through org_members (multi-org users).
    code = " ".join(split_statements(_migrations_sql()))
    assert not re.search(r"auth\.jwt\(\)\s*->>?\s*'org_id'", code)


def test_auth_uid_calls_are_statement_scoped() -> None:
    # Written as a scalar subquery, (select auth.uid()) and (select app.member_org_ids()) are
    # evaluated once per statement; a bare call is evaluated once per row (Architecture.md §9).
    # Calls, not mentions: comments (kept verbatim inside $$ function bodies) and string literals
    # (0012 checks auth.uid()'s definition through 'auth.uid()'::regprocedure) are not calls.
    sql = re.sub(r"--[^\n]*", "", _migrations_sql())
    code = re.sub(r"'(?:[^']|'')*'", "''", " ".join(split_statements(sql)))
    for call in ("auth.uid()", "app.member_org_ids()"):
        n = code.count(call) - code.count(f"function {call}")  # calls, not the definition
        assert n > 0, f"{call} not used; update this test"
        assert code.count(f"(select {call})") == n, f"bare {call} in a migration"
