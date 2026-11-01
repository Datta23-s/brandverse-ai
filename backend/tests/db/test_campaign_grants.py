"""0043: no API role can write a campaign, or insert an approval, by grant (not only by policy).

Same class as 0029 and 0030 for memory_items. `campaigns.status` and `approved_by` and
`approvals.outcome` decide what counts as approved; the API roles held Supabase's default
table-level INSERT and UPDATE on them since 0007 and 0009, blocked only by the absence of a
row-level-security policy. Now the grant is gone, so a policy added in error would still admit nothing.
"""

import pytest

from tests.db.conftest import Tx

pytestmark = pytest.mark.db

CAMPAIGN_COLUMNS = (
    "status",
    "approved_by",
    "approved_at",
    "creative",
    "validation",
    "revision_round",
)
APPROVAL_COLUMNS = ("outcome", "approver_id", "reason", "revision_round", "kind", "row_hash")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE"])
def test_no_api_role_holds_a_write_on_campaigns_whatever_the_column(
    tx: Tx, role: str, privilege: str
) -> None:
    rows = tx.execute(f"select has_table_privilege('{role}', 'public.campaigns', '{privilege}')")
    assert rows == [(False,)], f"{role} holds {privilege} on campaigns"
    for column in CAMPAIGN_COLUMNS:
        rows = tx.execute(
            f"select has_column_privilege('{role}', 'public.campaigns', '{column}', '{privilege}')"
        )
        assert rows == [(False,)], f"{role} holds {privilege} on campaigns.{column}"


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_api_role_holds_insert_on_approvals_whatever_the_column(tx: Tx, role: str) -> None:
    rows = tx.execute(f"select has_table_privilege('{role}', 'public.approvals', 'INSERT')")
    assert rows == [(False,)], f"{role} holds INSERT on approvals"
    for column in APPROVAL_COLUMNS:
        rows = tx.execute(
            f"select has_column_privilege('{role}', 'public.approvals', '{column}', 'INSERT')"
        )
        assert rows == [(False,)], f"{role} holds INSERT on approvals.{column}"


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_the_members_read_is_unchanged(tx: Tx, role: str) -> None:
    for table in ("campaigns", "approvals"):
        rows = tx.execute(f"select has_table_privilege('{role}', 'public.{table}', 'SELECT')")
        assert rows == [(True,)], f"{role} lost SELECT on {table}"


@pytest.mark.parametrize("table", ["campaigns", "approvals"])
def test_the_backend_role_still_writes_both(tx: Tx, table: str) -> None:
    for privilege in ("SELECT", "INSERT"):
        rows = tx.execute(
            f"select has_table_privilege('service_role', 'public.{table}', '{privilege}')"
        )
        assert rows == [(True,)], f"service_role lost {privilege} on {table}"
    rows = tx.execute("select has_table_privilege('service_role', 'public.campaigns', 'UPDATE')")
    assert rows == [(True,)]  # the pipeline parks and settles the campaign


def test_a_signed_in_approver_cannot_approve_their_own_campaign_by_a_direct_write(tx: Tx) -> None:
    """The end the grant protects: through the REST API a member writes with their own role."""
    from psycopg import errors

    tx.as_user("approver_a")
    with pytest.raises(errors.InsufficientPrivilege), tx.conn.transaction():
        tx.execute(
            "update campaigns set status = 'approved', approved_by = %s, approved_at = now() "
            "where id = %s",
            (tx.seed.users["approver_a"], tx.seed.campaign_a),
        )
