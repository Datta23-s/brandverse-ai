"""No credentials for the live project in CI (Rules.md P16), enforced by default-deny.

A workflow may read only the secrets on ALLOWED_SECRETS, and may name no Supabase host. A new
secret therefore stops the build until the diff that adds it also adds a line here saying why it
is fine, where review sees both. The same holds for a pooler or project host, which no workflow
needs: CI builds its own database and plays Supabase Auth itself (.github/workflows/ci.yml).

A pasted literal key is caught by prefix, for the two formats Supabase keys take: legacy keys are
JWTs and begin "eyJ"; this project's own keys (.env.example) begin "sb_secret_" (the publishable
"sb_publishable_" placeholders CI uses are not secret and are not matched). That is deliberately
NOT a general secret scanner: a credential of another shape pasted into a workflow passes this
test. It covers the credential that matters here, and nobody should read it as complete.

The guard is shown to fail on synthetic fragments below, so it is known to work, not assumed.
"""

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# secret name -> why a workflow may read it. Adding a name is a decision, made in review.
ALLOWED_SECRETS: dict[str, str] = {
    "GITHUB_TOKEN": "GitHub's own per-run token for this repository; not a project credential",
}

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_NAMED = re.compile(r"""\bsecrets\.(\w+)|\bsecrets\[\s*['"]([^'"]+)['"]\s*\]""")
_LITERAL_KEY = re.compile(r"eyJ|sb_secret_")
_HOST = re.compile(r"[\w.-]*supabase\.(?:co|com|net|in)\b", re.IGNORECASE)


def violations(workflow: str) -> list[str]:
    """What in this workflow text P16 forbids: a secret not on the allowlist, the whole `secrets`
    context (which reaches every secret), `secrets: inherit`, or a Supabase host."""
    found: list[str] = []
    for expression in _EXPRESSION.findall(workflow):
        named = _NAMED.findall(expression)
        for dotted, bracketed in named:
            name = dotted or bracketed
            if name not in ALLOWED_SECRETS:
                found.append(f"secret {name!r} is not on the allowlist")
        if re.search(r"\bsecrets\b", _NAMED.sub("", expression)):
            found.append("the whole `secrets` context is read, which reaches every secret")
    if re.search(r"^\s*secrets\s*:\s*inherit\b", workflow, re.MULTILINE):
        found.append("`secrets: inherit` passes every secret to another workflow")
    found += [f"Supabase host {m.group(0)!r}" for m in _HOST.finditer(workflow)]
    found += [f"a literal key beginning {m.group(0)!r}" for m in _LITERAL_KEY.finditer(workflow)]
    return found


# ── The guard fails when it should ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fragment", "why"),
    [
        ("env:\n  KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}\n", "SUPABASE_SERVICE_KEY"),
        ("env:\n  URL: ${{ secrets['DATABASE_URL'] }}\n", "DATABASE_URL"),
        ("run: echo ${{ toJSON(secrets) }}\n", "whole `secrets` context"),
        ("uses: ./.github/workflows/x.yml\nsecrets: inherit\n", "secrets: inherit"),
        (
            "env:\n  DATABASE_URL: postgresql://postgres:x@aws-0-ap-south-1.pooler.supabase.com:5432/postgres\n",
            "pooler.supabase.com",
        ),
        ("env:\n  URL: https://abcdefghijklmnop.supabase.co\n", "supabase.co"),
        ("env:\n  KEY: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.x\n", "beginning 'eyJ'"),
        ("env:\n  KEY: sb_secret_abcdefghijklmnop0123456789\n", "beginning 'sb_secret_'"),
        ("# KEY: eyJhbGciOiJIUzI1NiJ9\n", "beginning 'eyJ'"),  # a comment is one edit from config
        ("env:\n  TOKEN: ${{ secrets.GITHUB_TOKEN }}\n  K: ${{ secrets.OTHER }}\n", "OTHER"),
    ],
    ids=[
        "service_key",
        "bracket_form",
        "whole_context",
        "inherit",
        "pooler_host",
        "project_host",
        "legacy_jwt_key",
        "secret_key",
        "key_in_a_comment",
        "allowed_beside_disallowed",
    ],
)
def test_the_guard_rejects_what_p16_forbids(fragment: str, why: str) -> None:
    found = violations(fragment)
    assert found, "the guard accepted a fragment P16 forbids"
    assert any(why in f for f in found), found


def test_the_guard_accepts_the_allowlisted_token_and_ordinary_expressions() -> None:
    ok = (
        "env:\n  TOKEN: ${{ secrets.GITHUB_TOKEN }}\n  SHA: ${{ github.sha }}\n"
        "  NEXT_PUBLIC_SUPABASE_URL: http://127.0.0.1:9999\n"
    )
    assert violations(ok) == []


# ── The workflows as they are ────────────────────────────────────────────────────────────────


def test_no_workflow_holds_live_credentials() -> None:
    files = sorted(WORKFLOWS.glob("*.y*ml"))
    assert files, "no workflow files found: the scan reaches nothing"
    found = {f.name: v for f in files if (v := violations(f.read_text(encoding="utf-8")))}
    assert found == {}, f"Rules.md P16: {found}"
