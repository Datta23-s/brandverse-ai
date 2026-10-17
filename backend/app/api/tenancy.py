"""Organisations, brands and products (Architecture.md §10: setup CRUD).

Every query runs as the signed-in caller (`as_user`), so row-level security decides what they
may read and write, and every query also names its `org_id` explicitly (Rules.md P3). A caller
asking about an organisation they do not belong to gets 404, as if it did not exist.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row

from app.api.deps import IdentityDep, ResourcesDep, require_db
from app.core.db import as_user
from app.core.tenancy import has_role, is_member
from app.models.tenancy import (
    Brand,
    BrandCreate,
    Me,
    Organisation,
    OrganisationCreate,
    Product,
    ProductCreate,
)

router = APIRouter(prefix="/api/v1", tags=["setup"])

ORG_NOT_FOUND = "no such organisation"
WRITE_ROLES = ["owner", "editor"]


async def _require_member(conn: AsyncConnection, org_id: UUID) -> None:
    if not await is_member(conn, org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, ORG_NOT_FOUND)


async def _require_role(conn: AsyncConnection, org_id: UUID, roles: list[str], action: str) -> None:
    await _require_member(conn, org_id)
    if not await has_role(conn, org_id, roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{action} needs {' or '.join(roles)}")


async def _rows(conn: AsyncConnection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


# ── The caller ───────────────────────────────────────────────────────────────────────────────


@router.get("/me")
async def me(resources: ResourcesDep, identity: IdentityDep) -> Me:
    """The caller and the organisations they belong to, with their roles in each."""
    async with as_user(require_db(resources), identity) as conn:
        rows = await _rows(
            conn,
            "select o.id, o.name, array_agg(m.role order by m.role) as roles "
            "from organisations o join org_members m on m.org_id = o.id "
            "where o.id in (select app.member_org_ids()) "
            "and m.user_id = %s and m.revoked_at is null "
            "group by o.id, o.name, o.created_at order by o.created_at, o.name",
            (identity.user_id,),
        )
    return Me(
        user_id=identity.user_id,
        email=identity.email,
        organisations=[Organisation.model_validate(r) for r in rows],
    )


@router.post("/organisations", status_code=status.HTTP_201_CREATED)
async def create_organisation(
    resources: ResourcesDep, identity: IdentityDep, body: OrganisationCreate
) -> Organisation:
    """Create an organisation; the caller becomes its owner (app.create_organisation, 0012)."""
    async with as_user(require_db(resources), identity) as conn:
        try:
            cur = await conn.execute("select app.create_organisation(%s)", (body.name,))
        except errors.InvalidParameterValue as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, str(e.diag.message_primary)
            ) from e
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "organisation not created")
    return Organisation(id=row[0], name=body.name, roles=["owner"])


# ── Brands ───────────────────────────────────────────────────────────────────────────────────


@router.get("/brands")
async def list_brands(
    resources: ResourcesDep, identity: IdentityDep, org_id: Annotated[UUID, Query()]
) -> list[Brand]:
    async with as_user(require_db(resources), identity) as conn:
        await _require_member(conn, org_id)
        rows = await _rows(
            conn,
            "select id, org_id, name, created_at from brands where org_id = %s "
            "order by created_at, name",
            (org_id,),
        )
    return [Brand.model_validate(r) for r in rows]


@router.post("/brands", status_code=status.HTTP_201_CREATED)
async def create_brand(resources: ResourcesDep, identity: IdentityDep, body: BrandCreate) -> Brand:
    async with as_user(require_db(resources), identity) as conn:
        await _require_role(conn, body.org_id, WRITE_ROLES, "creating a brand")
        rows = await _rows(
            conn,
            "insert into brands (org_id, name) values (%s, %s) "
            "returning id, org_id, name, created_at",
            (body.org_id, body.name),
        )
    return Brand.model_validate(rows[0])


@router.delete("/brands/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(
    resources: ResourcesDep,
    identity: IdentityDep,
    brand_id: UUID,
    org_id: Annotated[UUID, Query()],
) -> Response:
    async with as_user(require_db(resources), identity) as conn:
        await _require_role(conn, org_id, ["owner"], "deleting a brand")
        try:
            cur = await conn.execute(
                "delete from brands where id = %s and org_id = %s", (brand_id, org_id)
            )
        except errors.ForeignKeyViolation as e:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "brand has runs or memory items; it is kept"
            ) from e
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such brand")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Products ─────────────────────────────────────────────────────────────────────────────────


@router.get("/products")
async def list_products(
    resources: ResourcesDep,
    identity: IdentityDep,
    org_id: Annotated[UUID, Query()],
    brand_id: Annotated[UUID | None, Query()] = None,
) -> list[Product]:
    async with as_user(require_db(resources), identity) as conn:
        await _require_member(conn, org_id)
        rows = await _rows(
            conn,
            "select id, org_id, brand_id, name, category, created_at from products "
            "where org_id = %s and (%s::uuid is null or brand_id = %s) order by created_at, name",
            (org_id, brand_id, brand_id),
        )
    return [Product.model_validate(r) for r in rows]


@router.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    resources: ResourcesDep, identity: IdentityDep, body: ProductCreate
) -> Product:
    async with as_user(require_db(resources), identity) as conn:
        await _require_role(conn, body.org_id, WRITE_ROLES, "creating a product")
        try:
            rows = await _rows(
                conn,
                "insert into products (org_id, brand_id, name, category) values (%s, %s, %s, %s) "
                "returning id, org_id, brand_id, name, category, created_at",
                (body.org_id, body.brand_id, body.name, body.category),
            )
        except errors.ForeignKeyViolation as e:
            # The (brand_id, org_id) foreign key: the brand is not in this organisation.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such brand") from e
    return Product.model_validate(rows[0])


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    resources: ResourcesDep,
    identity: IdentityDep,
    product_id: UUID,
    org_id: Annotated[UUID, Query()],
) -> Response:
    async with as_user(require_db(resources), identity) as conn:
        await _require_role(conn, org_id, ["owner"], "deleting a product")
        try:
            cur = await conn.execute(
                "delete from products where id = %s and org_id = %s", (product_id, org_id)
            )
        except errors.ForeignKeyViolation as e:
            raise HTTPException(status.HTTP_409_CONFLICT, "product has runs; it is kept") from e
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such product")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
