"""A product's truth, read once for the gates that judge a run against it: its claims, its brand's
sector and that sector's registry rules (Architecture.md §8, §8.2).

Read as the signed-in caller (row-level security applies; every query names the org,
Rules.md P3), or by the backend's own role in a worker. Retired claims are read too: a statement
citing a retired claim must be recognised as such, not as an unknown id.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection

from app.core.sector_registry import Sector, SectorConstraint

ClaimType = Literal["verified", "deduced", "restricted"]


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    text: str
    claim_type: ClaimType
    evidence_url: str | None
    retired: bool


@dataclass(frozen=True, slots=True)
class ProductTruth:
    sector: Sector | None
    claims: tuple[Claim, ...]
    rules: tuple[SectorConstraint, ...]

    def active(self, claim_type: ClaimType) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.claim_type == claim_type and not c.retired)

    def claim(self, claim_id: str) -> Claim | None:
        return next((c for c in self.claims if c.id == claim_id), None)


async def load_product_truth(
    conn: AsyncConnection, org_id: UUID, product_id: UUID
) -> ProductTruth | None:
    """The product's truth, or None if the organisation has no such product."""
    cur = await conn.execute(
        "select b.sector, s.regulated from products p "
        "join brands b on b.id = p.brand_id and b.org_id = p.org_id "
        "left join sectors s on s.id = b.sector "
        "where p.id = %s and p.org_id = %s",
        (product_id, org_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    sector_id, regulated = row
    sector = None if sector_id is None else Sector(sector_id, bool(regulated))

    cur = await conn.execute(
        "select id, claim, claim_type, evidence_url, retired_at is not null from product_claims "
        "where org_id = %s and product_id = %s order by created_at, id",
        (org_id, product_id),
    )
    claims = tuple(
        Claim(str(i), text, kind, url, retired)
        for i, text, kind, url, retired in await cur.fetchall()
    )

    rules: tuple[SectorConstraint, ...] = ()
    if sector is not None:
        cur = await conn.execute(
            "select id, sector_id, kind, effective_from, retired_at, match_type, pattern, "
            "disclaimer_text from sector_constraints where sector_id = %s "
            "order by effective_from, id",
            (sector.id,),
        )
        rules = tuple(
            SectorConstraint(str(i), s, k, eff, ret, mt, pat, dis)
            for i, s, k, eff, ret, mt, pat, dis in await cur.fetchall()
        )
    return ProductTruth(sector, claims, rules)
