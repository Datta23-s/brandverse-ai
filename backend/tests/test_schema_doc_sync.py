"""Architecture.md §9 must state exactly the SQL in supabase/migrations (Rules.md §8).

Statements are compared in order after removing comments and collapsing whitespace, so the doc
may be laid out differently but can never describe a schema that is not the one that deploys.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


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


def _doc_sql() -> str:
    text = (REPO_ROOT / "Architecture.md").read_text(encoding="utf-8")
    section = text[text.index("## 9. Data model") : text.index("## 10. API surface")]
    blocks = re.findall(r"```sql\n(.*?)```", section, flags=re.S)
    assert blocks, "Architecture.md §9 has no sql block"
    return "\n".join(blocks)


def _migrations_sql() -> str:
    files = sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql"))
    assert files
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_architecture_section_9_matches_migrations() -> None:
    doc, mig = split_statements(_doc_sql()), split_statements(_migrations_sql())
    for n, (d, m) in enumerate(zip(doc, mig, strict=False)):
        assert d == m, f"statement {n} differs:\n doc: {d[:200]}\n mig: {m[:200]}"
    assert len(doc) == len(mig), f"doc has {len(doc)} statements, migrations {len(mig)}"


def test_splitter_handles_quotes_and_dollar_bodies() -> None:
    sql = "select 'a;b' ; create function f() returns int as $$ select 1; $$ language sql; -- x;\n"
    assert split_statements(sql) == [
        "select 'a;b'",
        "create function f() returns int as $$ select 1; $$ language sql",
    ]
