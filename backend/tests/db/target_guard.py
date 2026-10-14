"""Refuse to point destructive database tooling at a managed Supabase instance.

The database tests and the storage measurement script drop and recreate a database. A mistyped
variable (DATABASE_URL pasted into TEST_DATABASE_URL) would destroy the project, so this is
enforced here, not left to a comment in .env.example.
"""

from psycopg.conninfo import conninfo_to_dict

# Direct connections use db.<ref>.supabase.co; the Supavisor pooler uses
# <region>.pooler.supabase.com. Matching both domains also covers any other Supabase-hosted name.
MANAGED_DOMAINS = ("supabase.co", "supabase.com")


class ManagedSupabaseTarget(Exception):
    """The connection string names a managed Supabase host."""


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
