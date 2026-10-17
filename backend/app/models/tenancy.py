"""Organisations, brands and products: what the API accepts and returns (Architecture.md §10)."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, StringConstraints

from app.models.base import Contract

Role = Literal["owner", "editor", "approver", "viewer"]
# 1–120 characters after trimming, as app.create_organisation enforces for organisations.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class OrganisationCreate(Contract):
    name: Name


class Organisation(Contract):
    id: UUID
    name: str
    roles: list[Role]


class Me(Contract):
    user_id: UUID
    email: str | None
    organisations: list[Organisation]


class BrandCreate(Contract):
    org_id: UUID
    name: Name


class Brand(Contract):
    id: UUID
    org_id: UUID
    name: str
    created_at: AwareDatetime


class ProductCreate(Contract):
    org_id: UUID
    brand_id: UUID
    name: Name
    category: Category | None = None


class Product(Contract):
    id: UUID
    org_id: UUID
    brand_id: UUID
    name: str
    category: str | None
    created_at: AwareDatetime
