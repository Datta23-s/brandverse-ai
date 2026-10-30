"""Refuse to point destructive database tooling at a managed Supabase instance.

The database tests and the storage measurement script drop and recreate a database. A mistyped
variable (DATABASE_URL pasted into TEST_DATABASE_URL) would destroy the project, so this is
enforced here, not left to a comment in .env.example.

The same guard refuses a host spelled `localhost`: on this laptop it tries `::1` first and takes
about 10 seconds per connection to fall back (Architecture.md section 11), so a suite of a few
hundred connections looks hung. It cost fourteen minutes twice; a documented rule was not enough.
"""

from psycopg.conninfo import conninfo_to_dict

# Direct connections use db.<ref>.supabase.co; the Supavisor pooler uses
# <region>.pooler.supabase.com. Matching both domains also covers any other Supabase-hosted name.
MANAGED_DOMAINS = ("supabase.co", "supabase.com")


class ManagedSupabaseTarget(Exception):
    """The connection string names a managed Supabase host."""


class LocalhostByName(ManagedSupabaseTarget):
    """The connection string says `localhost`. Subclass, so every caller that stops on the first
    stops on this too."""


def managed_supabase_hosts(conninfo: str) -> list[str]:
    """Hosts in the connection string that belong to managed Supabase. Accepts the URI and
    key=value forms; a comma-separated multi-host list is checked host by host."""
    host = conninfo_to_dict(conninfo).get("host") or ""
    hosts = [h.strip().lower().rstrip(".") for h in str(host).split(",") if h.strip()]
    return [h for h in hosts if any(h == d or h.endswith("." + d) for d in MANAGED_DOMAINS)]


def check_not_managed_supabase(conninfo: str, variable: str = "TEST_DATABASE_URL") -> None:
    """Raise ManagedSupabaseTarget, before any connection is opened, if the target is Supabase."""
    managed = managed_supabase_hosts(conninfo)
    if managed:
        raise ManagedSupabaseTarget(
            f"Refusing to run: {variable} points at a managed Supabase host "
            f"({', '.join(managed)}). This tooling drops and recreates a database; point "
            f"{variable} at a throwaway server (local Docker or the CI service container), "
            "never at the Supabase project."
        )
    if any(
        h.strip().lower().rstrip(".") == "localhost"
        for h in str(conninfo_to_dict(conninfo).get("host") or "").split(",")
    ):
        raise LocalhostByName(
            f"Refusing to run: {variable} names its host `localhost`. On this machine `localhost` "
            "tries the IPv6 address ::1 first and falls back to IPv4 only after a timeout of about "
            "10 seconds per connection, so the suite crawls and looks hung. Use 127.0.0.1 "
            "(Architecture.md section 11)."
        )
