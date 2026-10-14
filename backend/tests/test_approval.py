"""Approver-role constraint on campaigns.approved_by, application layer (Rules.md T6)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.approval import Membership, authorize_approval
from app.errors import ApprovalNotPermitted

ORG, OTHER_ORG, USER = uuid4(), uuid4(), uuid4()


def test_active_approver_in_campaign_org_may_approve() -> None:
    authorize_approval(USER, ORG, [Membership(ORG, USER, "approver")])


@pytest.mark.parametrize("role", ["owner", "editor", "viewer"])
def test_other_roles_may_not_approve(role: str) -> None:
    with pytest.raises(ApprovalNotPermitted):
        authorize_approval(USER, ORG, [Membership(ORG, USER, role)])  # type: ignore[arg-type]


def test_approver_of_another_org_may_not_approve() -> None:
    with pytest.raises(ApprovalNotPermitted):
        authorize_approval(USER, ORG, [Membership(OTHER_ORG, USER, "approver")])


def test_revoked_approver_may_not_approve() -> None:
    revoked = Membership(ORG, USER, "approver", revoked_at=datetime(2026, 10, 1, tzinfo=UTC))
    with pytest.raises(ApprovalNotPermitted):
        authorize_approval(USER, ORG, [revoked])


def test_another_users_approver_role_does_not_count() -> None:
    with pytest.raises(ApprovalNotPermitted):
        authorize_approval(USER, ORG, [Membership(ORG, uuid4(), "approver")])


def test_owner_who_also_holds_approver_may_approve() -> None:
    authorize_approval(
        USER, ORG, [Membership(ORG, USER, "owner"), Membership(ORG, USER, "approver")]
    )


def test_no_memberships_may_not_approve() -> None:
    with pytest.raises(ApprovalNotPermitted):
        authorize_approval(USER, ORG, [])
