"""Is the Supabase project active or paused? (Architecture.md §11, "Restoring a paused project")

    uv run python scripts/project_state.py

The free plan pauses an inactive project, and two of the owner's organisation's three projects were
already paused (2026-11-04). `brandverse-ai` must stay live through 25 November, including the
evaluation runs and the 16 November rating, so a pause is caught here, at startup, not mid-run.

The probe is a plain GET of the project's PUBLIC JWKS
(`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`), the same document the API verifies tokens
against: an active project answers 200 with a `keys` list. A paused project does not, and
anything else (DNS failure, timeout, 5xx, a body without keys) is reported as NOT ACTIVE. The
tool cannot tell a pause from a network outage, so it says "paused or unreachable" and the
runbook step is to look at the dashboard. It sends no credential and reads nothing but the
public key set. Exit 0 only for ACTIVE.

It reads the project's reachability, not its activity clock: whether a request here counts as the
"activity" that resets the one-week timer is not documented in this repository, so the weekly touch
in §11 stays a real database session (`scripts/live_posture.py` opens one).
"""

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

Fetch = Callable[[str], tuple[int, bytes]]
RESTORE = "Supabase dashboard -> project brandverse-ai -> Restore project (Architecture.md §11)"


def _real_fetch(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "BrandverseProjectState/0.1"})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            return r.status, r.read(1_000_000)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e).encode()


def project_state(supabase_url: str | None, fetch: Fetch = _real_fetch) -> tuple[bool, str]:
    """(active, message). Pure apart from `fetch`."""
    if not supabase_url:
        return False, "SUPABASE_URL is not set: the project's state cannot be read"
    url = supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    status, body = fetch(url)
    if status == 200:
        try:
            keys = json.loads(body).get("keys")
        except (ValueError, AttributeError):
            keys = None
        if isinstance(keys, list) and keys:
            return True, f"ACTIVE: the project's key set answered ({len(keys)} key(s))"
        return False, "NOT ACTIVE: HTTP 200 but no key set in the body"
    what = f"HTTP {status}" if status else f"no answer ({body.decode(errors='replace')[:80]})"
    return False, f"NOT ACTIVE (paused or unreachable): {what}. Look at the dashboard: {RESTORE}"


def main(fetch: Fetch = _real_fetch) -> int:
    from app.core.config import get_settings

    url = get_settings().supabase_url
    ok, message = project_state(str(url) if url else None, fetch)
    print("project state:", message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
