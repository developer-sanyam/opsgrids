"""Request and response contracts for the account service.

This module is the published API surface: a change here is a contract change, and the
frontend generates its client from it.

Two shapes worth noting. `MemberOut` is a **membership** flattened with the parts of the
person a colleague may see — never the whole user row, because a user exists across
organizations and one customer must not learn where else somebody works. And nothing here
carries `password_hash`, which is why responses are built field by field rather than
serialising the ORM object wholesale.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Scope = Literal["OWN", "TEAM", "LOCATION", "ORG"]
Slug = Annotated[str, Field(min_length=2, max_length=60, pattern=r"^[a-z0-9][a-z0-9-]*$")]
Code = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- organizations


class OrganizationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: Slug
    legal_name: str | None = Field(default=None, max_length=240)
    industry: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    state_code: str | None = Field(default=None, max_length=10)
    timezone: str = "Asia/Kolkata"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    locale: str = "en-IN"
    fiscal_year_start_month: int = Field(default=4, ge=1, le=12)
    primary_contact_email: EmailStr | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class OrganizationPatch(BaseModel):
    """Partial update. Absent means "leave alone" — a PATCH cannot null a field."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=240)
    industry: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    state_code: str | None = Field(default=None, max_length=10)
    timezone: str | None = None
    locale: str | None = None
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
    primary_contact_email: EmailStr | None = None
    tax_ids: dict[str, Any] | None = None
    registered_address: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    legal_name: str | None
    industry: str | None
    status: str
    country: str | None
    state_code: str | None
    timezone: str
    currency: str
    locale: str
    fiscal_year_start_month: int
    tax_ids: dict[str, Any]
    registered_address: dict[str, Any] | None
    primary_contact_email: str | None
    settings: dict[str, Any]
    parent_org_id: uuid.UUID | None
    trial_ends_at: datetime | None
    created_at: datetime


# --------------------------------------------------------------------- structure


class SignupIn(BaseModel):
    """A stranger creating an organisation and becoming its owner (ADR-0013 §3).

    **No password.** The owner sets one through the reset-link flow after proving the address
    receives mail; a payload with a password here would be a credential held by a service that
    has no business holding one, on an address nobody has verified.
    """

    organization_name: str = Field(min_length=1, max_length=200)
    slug: Slug
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr


class SignupOut(BaseModel):
    organization_id: uuid.UUID
    slug: str
    email: str
    #: Always the same sentence. What the dashboard shows next, and a reminder that the answer
    #: to "did that work?" is in the inbox, not in this body.
    next_step: str = "Check your inbox for the link that sets your password."


class OrgUnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: Code
    parent_id: uuid.UUID | None = None
    kind: str = Field(default="department", max_length=24)


class OrgUnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    parent_id: uuid.UUID | None
    head_membership_id: uuid.UUID | None
    kind: str
    is_active: bool


class OrgUnitNode(OrgUnitOut):
    """A unit with its children, so the tree renders in one call."""

    children: list[OrgUnitNode] = Field(default_factory=list)


class OrgUnitPatch(BaseModel):
    """Partial update. `code` is immutable — other services and tokens key on it."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    kind: str | None = Field(default=None, max_length=24)
    head_membership_id: uuid.UUID | None = None
    is_active: bool | None = None


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: Code
    timezone: str = "Asia/Kolkata"
    country: str | None = Field(default=None, min_length=2, max_length=2)
    address: dict[str, Any] | None = None


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    timezone: str
    country: str | None
    address: dict[str, Any] | None
    is_active: bool


class LocationPatch(BaseModel):
    """Partial update. `code` is immutable — it is embedded in members' `location_codes`
    and in tokens."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    timezone: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    address: dict[str, Any] | None = None
    is_active: bool | None = None


# --------------------------------------------------------------------- members


class MemberOut(BaseModel):
    """A membership, plus the parts of the person a colleague may see.

    Deliberately not the user row: a person exists across organizations, and one customer
    has no business learning where else they work.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    display_name: str | None
    job_title: str | None
    status: str
    is_owner: bool
    org_unit_id: uuid.UUID | None
    manager_membership_id: uuid.UUID | None
    location_codes: list[str]
    employee_id: uuid.UUID | None
    roles: list[str]
    joined_at: datetime | None
    started_on: date | None


class MemberPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    org_unit_id: uuid.UUID | None = None
    manager_membership_id: uuid.UUID | None = None
    location_codes: list[str] | None = None
    started_on: date | None = None
    #: The HR employee record this principal **is** — the sole origin of the `sref` claim
    #: every OWN-scoped check in the estate resolves against (ADR-0008 §1). This is the
    #: platform's only writer of that link.
    #:
    #: `exclude_unset` carries the distinction this field needs and no other field here
    #: has: **absent** leaves the link alone, an explicit **null unlinks**. It is not
    #: validated against employee-service — account cannot read another schema, and a
    #: synchronous cross-service check on an interactive write is what ADR-0008 §4
    #: refuses; a mistyped id is accepted and found by `tools/reconcile_membership_links.py`.
    employee_id: uuid.UUID | None = None


class MyGrantsOut(BaseModel):
    """What the caller may do, for the browser to hide controls with.

    Never the enforcement itself — every write still checks the permission server-side —
    but the one call every product's front end shares rather than re-deriving.
    """

    membership_id: uuid.UUID
    #: The permission version, when Redis has one to give. Omitted rather than defaulted to
    #: zero: a cache that has never been bumped is a different fact from a cache that is down,
    #: and a client cannot tell a stale `pv: 0` from a live one.
    pv: int | None = None
    grants: list[str]


class InvitationIn(BaseModel):
    email: EmailStr
    role_codes: list[Code] = Field(default_factory=list)
    product_codes: list[Code] = Field(default_factory=list)
    expires_in_days: int = Field(default=14, ge=1, le=90)


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role_codes: list[str]
    product_codes: list[str]
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreated(InvitationOut):
    """The one response that carries the raw token.

    Only the hash is stored, so this is the single moment the token exists outside the
    email that delivers it. It is never readable again.
    """

    token: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------------- roles


class GrantIn(BaseModel):
    permission_code: str = Field(min_length=1, max_length=160)
    scope: Scope = "OWN"


class GrantOut(BaseModel):
    permission_code: str
    scope: Scope


class RoleIn(BaseModel):
    code: Code
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    product: str | None = Field(default=None, max_length=40)
    grants: list[GrantIn] = Field(default_factory=list)


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    product: str | None
    is_default: bool
    is_system: bool
    grants: list[GrantOut]


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    product: str
    module: str
    resource: str
    action: str
    description: str | None
    default_scope: str
    max_scope: str
    is_dangerous: bool


class RoleAssignment(BaseModel):
    role_codes: list[Code]


# `OrgUnitNode` refers to itself; resolve the forward reference.
OrgUnitNode.model_rebuild()
