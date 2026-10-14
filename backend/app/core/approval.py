"""Who may approve a campaign (Rules.md T6).

Application-layer check, mirrored in the database by the campaigns approver FK and trigger
(supabase/migrations/0007_campaigns.sql). The API refuses early with a clear error; the database
remains the backstop if this check is ever bypassed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.errors import ApprovalNotPermitted

Role = Literal["owner", "editor", "approver", "viewer"]


@dataclass(frozen=True)
class Membership:
    org_id: UUID
    user_id: UUID
    role: Role
    revoked_at: datetime | None = None


def authorize_approval(
    actor_id: UUID, campaign_org_id: UUID, memberships: Iterable[Membership]
) -> None:
    """Raise ApprovalNotPermitted unless the actor holds an unrevoked approver role in the
    campaign's own organisation. Owner, editor and viewer roles do not confer approval."""
    for m in memberships:
        if (
            m.user_id == actor_id
            and m.org_id == campaign_org_id
            and m.role == "approver"
            and m.revoked_at is None
        ):
            return
    raise ApprovalNotPermitted(
        f"user {actor_id} holds no active approver role in organisation {campaign_org_id}"
    )
