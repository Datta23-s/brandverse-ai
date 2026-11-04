"""The project-state check reports a paused or unreachable Supabase project.

Offline: the fetch is injected, so no request leaves the machine.
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import project_state as ps  # noqa: E402

URL = "https://example-ref.supabase.co"


def _fetch(status: int, body: bytes = b"") -> ps.Fetch:
    def fetch(url: str) -> tuple[int, bytes]:
        assert url == URL + "/auth/v1/.well-known/jwks.json"
        return status, body

    return fetch


def test_an_answering_project_is_active() -> None:
    body = json.dumps({"keys": [{"kid": "a"}]}).encode()
    ok, msg = ps.project_state(URL + "/", _fetch(200, body))
    assert ok and msg.startswith("ACTIVE")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (0, b"getaddrinfo failed"),
        (540, b""),
        (503, b""),
        (404, b""),
        (200, b"{}"),
        (200, b"not json"),
        (200, b'{"keys": []}'),
    ],
)
def test_anything_but_a_key_set_is_not_active(status: int, body: bytes) -> None:
    ok, msg = ps.project_state(URL, _fetch(status, body))
    assert not ok and msg.startswith("NOT ACTIVE")
    if status != 200:
        assert "Restore project" in msg


def test_an_unset_url_is_not_active() -> None:
    ok, msg = ps.project_state(None, _fetch(200))
    assert not ok and "SUPABASE_URL" in msg


def test_the_live_posture_tool_checks_state_before_it_connects() -> None:
    src = (BACKEND / "scripts" / "live_posture.py").read_text(encoding="utf-8")
    assert src.index("project_state.main()") < src.index("open_read_only(args.url)")
