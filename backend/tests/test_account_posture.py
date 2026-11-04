"""The account-identity guard (Rules.md P17) refuses an account outside config/accounts.yaml.

Offline: `observe` is driven with a fake `gcloud` and `git`, so no real account is read.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import account_posture as ap  # noqa: E402

OK = "dattasable1213@gmail.com"
KUNDAN = "kundansable@gmail.com"


def _fake(gcloud_active: str = OK, author: str = OK, owner: str = "Datta23-s",
          members: str = f"user:{OK}", credentialed: str | None = None) -> ap.Run:
    def run(cmd: list[str]) -> tuple[int, str]:
        if cmd[:3] == ["gcloud", "config", "list"]:
            return 0, gcloud_active
        if cmd[:3] == ["gcloud", "auth", "list"]:
            return 0, credentialed if credentialed is not None else gcloud_active
        if cmd[:2] == ["git", "config"]:
            return 0, author
        if cmd[:2] == ["git", "remote"]:
            return 0, f"https://github.com/{owner}/brandverse-ai.git"
        if cmd[:3] == ["gcloud", "projects", "get-iam-policy"]:
            return 0, members
        raise AssertionError(cmd)

    return run


def _check(run: ap.Run) -> list[str]:
    d = ap.load_declared()
    failures, _ = ap.evaluate(d, ap.observe(d, run))
    return failures


def test_the_declared_set_is_datta_and_never_kundan() -> None:
    d = ap.load_declared()
    assert OK in d.allowed_emails
    assert KUNDAN not in d.allowed_emails
    assert KUNDAN in d.known_disallowed_emails


def test_datta_everywhere_passes() -> None:
    assert _check(_fake()) == []


@pytest.mark.parametrize(
    ("run", "needle"),
    [
        (_fake(gcloud_active=KUNDAN), "gcloud active account is kundansable@gmail.com"),
        (_fake(credentialed=f"{OK} {KUNDAN}"), "gcloud credentialed account is"),
        (_fake(author=KUNDAN), "git author email is kundansable@gmail.com"),
        (_fake(owner="kundan-s"), "git origin owner is kundan-s"),
        (_fake(members=f"user:{OK} user:{KUNDAN}"), "Gemini project IAM member is"),
        (_fake(gcloud_active="someone@example.org"), "not in config/accounts.yaml"),
    ],
)
def test_an_account_outside_the_set_is_refused(run: ap.Run, needle: str) -> None:
    failures = _check(run)
    assert failures and any(needle in f for f in failures), failures


def test_the_named_account_is_called_out() -> None:
    assert "disallowed for project credentials" in _check(_fake(gcloud_active=KUNDAN))[0]


def test_only_project_credentials_are_read() -> None:
    """The Claude Code login and connectors are out of scope: only gcloud and git are ever run."""
    seen: list[str] = []
    base = _fake()

    def run(cmd: list[str]) -> tuple[int, str]:
        seen.append(cmd[0])
        return base(cmd)

    _check(run)
    assert set(seen) == {"gcloud", "git"}


def test_gcloud_signed_out_fails_closed() -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        return (1, "") if cmd[0] == "gcloud" else _fake()(cmd)

    failures = _check(run)
    assert any("no active account" in f for f in failures)


def test_unreadable_iam_is_reported_not_passed() -> None:
    def run(cmd: list[str]) -> tuple[int, str]:
        if cmd[:3] == ["gcloud", "projects", "get-iam-policy"]:
            return 1, ""
        return _fake()(cmd)

    d = ap.load_declared()
    failures, not_checked = ap.evaluate(d, ap.observe(d, run))
    assert failures == []
    assert any("IAM members" in n for n in not_checked)


def test_main_exits_nonzero_for_an_outside_account() -> None:
    assert ap.main(_fake(gcloud_active=KUNDAN)) == 1
    assert ap.main(_fake()) == 0


def test_the_live_posture_tool_runs_the_account_check_before_it_connects() -> None:
    src = (BACKEND / "scripts" / "live_posture.py").read_text(encoding="utf-8")
    assert src.index("account_posture.main()") < src.index("open_read_only(args.url)")
