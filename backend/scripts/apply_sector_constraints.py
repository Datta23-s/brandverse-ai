"""The registry's reviewed authoring path (Architecture.md §8.1: "Rows are written by platform
admins through a reviewed path; no agent or LLM output ever writes a row").

A rule set is a YAML file under config/sector_constraints/, reviewed and committed in git like code.
This script applies it to a database with the backend's own role:

- **Dry run by default**; `--apply` writes. Every change is printed before it is made.
- **Committed files only.** A file with uncommitted changes is refused, and the commit that holds
  it is recorded in every audit row — the review trail is git's.
- **The author is a person** in auth.users, named by email (`created_by`, a human; 0002).
- **Append and retire, never edit or delete** (the 0002 trigger): a new entry is inserted; an entry
  given `retired_at` retires its row; an active cited rule of the sector that is missing from the
  file stops the run — rules are retired explicitly, never by deletion from a file.
- **Cited rules only.** Every entry needs an http(s) `source_url`. Synthetic rules exist only as
  migration 0021 seeded them; a file cannot add one, nor mark a sector authoritative.
- **Parameterised inserts only** (Rules.md §3.3): patterns and their backslashes reach the database
  as query parameters, never through string building.
- **Every change is written to audit_log** (`registry.rule_added`, `registry.rule_retired`), with
  the author as actor and the file and commit in `details`.

Usage (from backend/):
  uv run python -m scripts.apply_sector_constraints ../config/sector_constraints/<sector>.yaml \\
      --author-email someone@example.com [--apply]
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Self

import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import REPO_ROOT, get_settings

SYNTHETIC_NOTE = "Synthetic rule, not derived from any regulation. Illustrative only."


class RuleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["required_disclaimer", "prohibited_phrase"]
    match_type: Literal["phrase", "regex"]
    pattern: str | None = None
    disclaimer_text: str | None = None
    source_url: str = Field(pattern=r"^https?://\S+$")
    source_note: str | None = None
    effective_from: date
    retired_at: date | None = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.kind == "prohibited_phrase" and (self.pattern is None or self.disclaimer_text):
            raise ValueError("a prohibited_phrase needs a pattern and no disclaimer_text")
        if self.kind == "required_disclaimer" and not self.disclaimer_text:
            raise ValueError("a required_disclaimer needs disclaimer_text")
        if self.match_type == "regex" and self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"pattern does not compile: {e}") from e
        if self.source_note == SYNTHETIC_NOTE:
            raise ValueError("synthetic rules are seeded by migration 0021, not authored here")
        if self.retired_at is not None and self.retired_at < self.effective_from:
            raise ValueError("retired_at is before effective_from")
        return self

    def key(self) -> tuple[object, ...]:
        return (self.kind, self.match_type, self.pattern, self.disclaimer_text, self.source_url,
                self.effective_from)  # fmt: skip


class RuleFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sector: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    jurisdiction: str = Field(default="IN", pattern=r"^[A-Z]{2}$")
    rules: list[RuleEntry]


@dataclass(frozen=True)
class Change:
    action: Literal["add", "retire"]
    entry: RuleEntry
    row_id: str | None = None


class Refused(Exception):
    """The file cannot be applied as it stands; nothing was changed."""


EXISTING_SQL = """
select id, kind, match_type, pattern, disclaimer_text, source_url, effective_from, retired_at
from public.sector_constraints
where sector_id = %(sector)s and jurisdiction = %(jurisdiction)s and source_url is not null
"""
INSERT_SQL = """
insert into public.sector_constraints
  (sector_id, jurisdiction, kind, match_type, pattern, disclaimer_text, source_url, source_note,
   effective_from, retired_at, created_by)
values (%(sector)s, %(jurisdiction)s, %(kind)s, %(match_type)s, %(pattern)s, %(disclaimer_text)s,
        %(source_url)s, %(source_note)s, %(effective_from)s, %(retired_at)s, %(author)s)
returning id
"""
AUDIT_SQL = """
insert into public.audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id, details)
values (null, 'user', %(author)s, %(action)s, 'sector_constraint', %(entity)s, %(details)s)
"""


def plan(conn: psycopg.Connection, rule_file: RuleFile) -> list[Change]:
    """The changes applying `rule_file` would make. Raises Refused if it cannot be applied."""
    if (
        conn.execute("select 1 from public.sectors where id = %s", (rule_file.sector,)).fetchone()
        is None
    ):
        raise Refused(f"no such sector {rule_file.sector!r}")
    keys = [e.key() for e in rule_file.rules]
    if len(set(keys)) != len(keys):
        raise Refused("the file lists the same rule twice")
    rows = conn.execute(
        EXISTING_SQL, {"sector": rule_file.sector, "jurisdiction": rule_file.jurisdiction}
    ).fetchall()
    existing = {tuple(r[1:7]): (str(r[0]), r[7]) for r in rows}
    changes: list[Change] = []
    for entry in rule_file.rules:
        found = existing.get(entry.key())
        if found is None:
            changes.append(Change("add", entry))
            continue
        row_id, retired_at = found
        if retired_at == entry.retired_at:
            continue
        if retired_at is None:
            changes.append(Change("retire", entry, row_id))
        else:
            raise Refused(f"rule {row_id} is retired on {retired_at}; a file cannot change that")
    missing = [
        row_id
        for key, (row_id, retired_at) in existing.items()
        if retired_at is None and key not in set(keys)
    ]
    if missing:
        raise Refused(
            f"active rules of {rule_file.sector!r} are missing from the file: {missing}; "
            "retire a rule by giving it retired_at, never by deleting it from the file"
        )
    return changes


def apply(
    conn: psycopg.Connection, rule_file: RuleFile, author_email: str, source: dict[str, str]
) -> list[Change]:
    """Apply in the caller's transaction (the caller commits). Every change is audit-logged."""
    author = conn.execute(
        "select id from auth.users where lower(email) = lower(%s)", (author_email,)
    ).fetchone()
    if author is None:
        raise Refused(f"no user with email {author_email!r}: the author must be a person")
    changes = plan(conn, rule_file)
    for change in changes:
        e = change.entry
        if change.action == "add":
            row = conn.execute(
                INSERT_SQL,
                {
                    "sector": rule_file.sector,
                    "jurisdiction": rule_file.jurisdiction,
                    "kind": e.kind,
                    "match_type": e.match_type,
                    "pattern": e.pattern,
                    "disclaimer_text": e.disclaimer_text,
                    "source_url": e.source_url,
                    "source_note": e.source_note,
                    "effective_from": e.effective_from,
                    "retired_at": e.retired_at,
                    "author": author[0],
                },
            ).fetchone()
            if row is None:
                raise RuntimeError("insert returned no id")
            entity, action = str(row[0]), "registry.rule_added"
        else:
            conn.execute(
                "update public.sector_constraints set retired_at = %s where id = %s",
                (e.retired_at, change.row_id),
            )
            entity, action = str(change.row_id), "registry.rule_retired"
        conn.execute(
            AUDIT_SQL,
            {
                "author": str(author[0]),
                "action": action,
                "entity": entity,
                "details": json.dumps(
                    {"sector": rule_file.sector, **source,
                     "applied_by": "scripts/apply_sector_constraints.py"}
                ),
            },
        )  # fmt: skip
    return changes


def committed_source(path: Path) -> dict[str, str]:
    """The file's path and the commit that holds it; Refused if it has uncommitted changes."""
    rel = path.resolve().relative_to(REPO_ROOT)
    status = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "status", "--porcelain", "--", str(rel)],  # noqa: S607
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    if status.strip():
        raise Refused(
            f"{rel} has uncommitted changes: rule files are applied from reviewed commits"
        )
    commit = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "log", "-1", "--format=%H", "--", str(rel)],  # noqa: S607
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    if not commit:
        raise Refused(f"{rel} is not committed")
    return {"source_file": rel.as_posix(), "commit": commit}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--author-email", required=True)
    parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL must be set in .env")
        return 1
    try:
        rule_file = RuleFile.model_validate(yaml.safe_load(args.file.read_text(encoding="utf-8")))
        source = committed_source(args.file)
        with psycopg.connect(settings.database_url.get_secret_value()) as conn:
            changes = apply(conn, rule_file, args.author_email, source)
            for c in changes:
                print(f"{c.action}: {c.entry.kind} {c.entry.pattern or c.entry.disclaimer_text!r}")
            if not changes:
                print("nothing to change")
            if not args.apply:
                conn.rollback()
                print("dry run: rolled back; pass --apply to write")
                return 0
        print(f"applied {len(changes)} change(s) from {source['source_file']} @ {source['commit']}")
        return 0
    except Refused as e:
        print(f"refused, nothing changed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
