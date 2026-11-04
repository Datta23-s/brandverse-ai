"""Account-identity posture check (Rules.md P17): every account the project uses must be Datta's.

    uv run python scripts/account_posture.py

Reads the identities it CAN read, compares each with `config/accounts.yaml`, and exits non-zero if any
is outside the declared set or cannot be read when it must be:

* the gcloud active account, and every other credentialed gcloud account (required: gcloud missing or
  signed out is a failure, not a pass);
* the git author email, and the owner of the `origin` remote;
* the IAM members of the Cloud project that owns the Gemini API key (when gcloud can read them;
  "cannot read" is reported as NOT CHECKED and does not pass silently).

Not machine-readable, so ATTESTED by the owner instead (config/accounts.yaml `attested`, audit closed
2026-11-04): the Supabase project's owner and the GitHub account's email. Out of scope by design, and
never read: the Claude Code login and Claude's connectors, which the project does not authenticate
with (Rules.md P17 scope, Kundan 2026-11-04).

Read-only: it runs `gcloud` and `git` with fixed argument lists and writes nothing.
"""

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ACCOUNTS_PATH = REPO / "config" / "accounts.yaml"

Run = Callable[[list[str]], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class Declared:
    allowed_emails: frozenset[str]
    allowed_github_owners: frozenset[str]
    known_disallowed_emails: frozenset[str]
    gemini_cloud_project_id: str


@dataclass(slots=True)
class Observed:
    """What was read. `None` means it could not be read."""

    gcloud_active: str | None = None
    gcloud_credentialed: list[str] | None = None
    git_author_email: str | None = None
    git_origin_owner: str | None = None
    project_members: list[str] | None = None
    notes: list[str] = field(default_factory=list)


def load_declared(path: Path = ACCOUNTS_PATH) -> Declared:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    emails = frozenset(str(e).lower() for e in raw["allowed_emails"])
    if not emails:
        raise ValueError("config/accounts.yaml allows no account")
    return Declared(
        allowed_emails=emails,
        allowed_github_owners=frozenset(str(o) for o in raw["allowed_github_owners"]),
        known_disallowed_emails=frozenset(str(e).lower() for e in raw["known_disallowed_emails"]),
        gemini_cloud_project_id=str(raw["gemini_cloud_project_id"]),
    )


def _email_problem(label: str, email: str, d: Declared) -> str | None:
    e = email.lower()
    if e in d.allowed_emails:
        return None
    named = " (disallowed for project credentials)" if e in d.known_disallowed_emails else ""
    return f"{label} is {email}{named}: not in config/accounts.yaml allowed_emails"


def evaluate(d: Declared, o: Observed) -> tuple[list[str], list[str]]:
    """(failures, not_checked). Pure, so it can be driven with any account."""
    failures: list[str] = []
    not_checked: list[str] = []
    if not o.gcloud_active:
        failures.append("gcloud has no active account (required: sign in as an allowed account)")
    elif p := _email_problem("gcloud active account", o.gcloud_active, d):
        failures.append(p)
    for acct in o.gcloud_credentialed or []:
        if p := _email_problem("gcloud credentialed account", acct, d):
            failures.append(p)
    if o.git_author_email is None:
        failures.append("git user.email is not set")
    elif p := _email_problem("git author email", o.git_author_email, d):
        failures.append(p)
    if o.git_origin_owner is None:
        failures.append("git origin remote owner could not be read")
    elif o.git_origin_owner not in d.allowed_github_owners:
        failures.append(
            f"git origin owner is {o.git_origin_owner}: not in config/accounts.yaml "
            "allowed_github_owners"
        )
    if o.project_members is None:
        not_checked.append(f"IAM members of Cloud project {d.gemini_cloud_project_id} (unreadable)")
    else:
        for m in o.project_members:
            if p := _email_problem("Gemini project IAM member", m, d):
                failures.append(p)
    return failures, not_checked


def _real_run(cmd: list[str]) -> tuple[int, str]:
    exe = shutil.which(cmd[0])  # gcloud is gcloud.cmd on Windows, which a bare name does not find
    if exe is None:
        return 127, f"{cmd[0]} not found"
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, r.stdout.strip()


def observe(d: Declared, run: Run = _real_run) -> Observed:
    o = Observed()
    code, out = run(["gcloud", "config", "list", "--format=value(core.account)"])
    o.gcloud_active = out.strip() or None if code == 0 else None
    code, out = run(["gcloud", "auth", "list", "--format=value(account)"])
    o.gcloud_credentialed = [x for x in out.split() if x] if code == 0 else None
    code, out = run(["git", "config", "user.email"])
    o.git_author_email = out.strip() or None if code == 0 else None
    code, out = run(["git", "remote", "get-url", "origin"])
    if code == 0 and (m := re.search(r"github\.com[:/]([^/]+)/", out)):
        o.git_origin_owner = m.group(1)
    code, out = run(
        [
            "gcloud",
            "projects",
            "get-iam-policy",
            d.gemini_cloud_project_id,
            "--flatten=bindings[].members",
            "--format=value(bindings.members)",
        ]
    )
    if code == 0:
        o.project_members = [
            x.split(":", 1)[1] for x in out.split() if x.startswith(("user:", "serviceAccount:"))
        ]
    return o


def main(run: Run = _real_run) -> int:
    d = load_declared()
    failures, not_checked = evaluate(d, observe(d, run))
    for f in failures:
        print("REFUSED", f)
    for n in not_checked:
        print("NOT CHECKED", n)
    print("account posture:", "FAILED" if failures else "every readable identity is allowed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
