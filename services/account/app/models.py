"""Tables owned by the account service — the organizational skeleton of the platform.

Everything here lives in the `account` schema and nothing outside it holds a foreign key
into it.

Two shapes carry most of the weight:

**`users` is global; `memberships` is per organization.** Email is unique across the whole
platform, and belonging to a company is a separate row. That is what lets one person work
for two customers and one account reach every product (ADR-0000 §1).

**`org_units` and `locations` live here, not in an HR product.** A scope dimension that
appears in a token has to be resolvable when the token is minted — and an organization
that never bought HRMS still has departments.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "account"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


# --------------------------------------------------------------------- organizations


class Organization(Base):
    """A customer. The tenant every other row in the platform belongs to.

    Deliberately not `TenantBase`: an organization is not scoped *by* an organization, it
    *is* one.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: URL segment and login hint — `acme` in `acme.opsgrids.com`.
    slug: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(240))
    industry: Mapped[str | None] = mapped_column(String(80))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="trial")

    #: Locale and finance defaults every product inherits rather than asking again.
    country: Mapped[str | None] = mapped_column(String(2))
    state_code: Mapped[str | None] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-IN")
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, nullable=False, default=4)

    tax_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    registered_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: require_mfa, session_ttl, ip_allowlist, password_min_length…
    security_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: A group running several trading names. Scoping stays **leaf-only** —
    #: `organization_id` means exactly one organization, never "or any descendant".
    #: Making it mean the latter everywhere is how a hierarchy becomes a data leak;
    #: roll-ups resolve the descendant set explicitly and are authorised separately.
    parent_org_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT")
    )

    primary_contact_email: Mapped[str | None] = mapped_column(CITEXT)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_organizations_parent", "parent_org_id"),)


class OrgDomain(Base):
    """An email domain belonging to an organization.

    A table rather than a column, because a real company has several — and a single unique
    column is the thing that forces an ugly migration later.

    `verified_at` is load-bearing: an **unverified** domain must never select an identity
    provider or auto-join anyone. Domain routing without proof of ownership is an
    account-takeover primitive.
    """

    __tablename__ = "org_domains"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Globally unique: a domain belongs to one organization.
    domain: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_join: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------- org structure


class OrgUnit(Base, TenantBase):
    """A department, division or team — the organizational tree.

    Generic on purpose. HR calls it a department, delivery calls it a squad, CRM uses it
    as a territory; one node type serves all three and each product labels it.
    """

    __tablename__ = "org_units"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="RESTRICT")
    )
    #: Who leads it — a membership, not a user. Leadership is per organization.
    head_membership_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="department")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_org_units_org_code"),
        # Tenant-leading for the same reason; `descendant_ids` walks this recursively.
        Index("ix_org_units_parent", "organization_id", "parent_id"),
    )


class Location(Base, TenantBase):
    """A physical site, carrying the timezone its days are rendered in.

    Here rather than in an HR product because the token carries location codes for
    `LOCATION` scope, so they must resolve at mint time.
    """

    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: IANA zone. A day boundary belongs to the *site*, never to the viewer's browser.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    country: Mapped[str | None] = mapped_column(String(2))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_locations_org_code"),)


# --------------------------------------------------------------------- identity


class User(Base):
    """A person. **Global, not per organization.**

    Email is unique across the entire platform; everything organization-specific lives on
    `Membership`.

    Deliberately holds **no credentials**. Passwords, lockout state, MFA factors and
    sessions belong to auth-service: this service is the directory — who exists and where
    they belong — and authentication is a different job with a different blast radius.
    A password hash living in the directory is one careless serialiser away from an API
    response.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar_file_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    timezone: Mapped[str | None] = mapped_column(String(64))
    locale: Mapped[str | None] = mapped_column(String(16))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: OpsGrids staff. Never grants access to a customer's data on its own — the operator
    #: console checks this *and* audits every action taken through it.
    is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # Partial, so a soft-deleted account does not hold its address hostage forever.
        Index("uq_users_email", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )


class Membership(Base):
    """One person's belonging to one organization.

    This row is what a product means by "a user": it carries the roles, the department,
    the locations and the per-product subject reference.
    """

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: The HR employee record, when there is one. Here rather than on `users`, because a
    #: person can be an employee of one customer and a contractor for another. A bare
    #: UUID, never a foreign key — that link crosses a schema boundary.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    org_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="SET NULL")
    )
    #: Who this person reports to. Read directly, never walked transitively, so a cycle is
    #: untidy rather than dangerous and "can X see Y" stays one query.
    manager_membership_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Location codes for LOCATION scope, comma-separated.
    location_codes: Mapped[str | None] = mapped_column(String(255))

    display_name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(120))

    invited_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_on: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    roles: Mapped[list[MembershipRole]] = relationship(
        back_populates="membership", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_org"),
        Index("ix_memberships_org_status", "organization_id", "status"),
        # Leads with the tenant: under row-level security the policy qual is ANDed into
        # every query, so an index that does not would scan the platform and discard.
        Index("ix_memberships_manager", "organization_id", "manager_membership_id"),
        # One employee record maps to one membership, enforced in the database — the
        # alternative is two people sharing a payslip.
        Index(
            "uq_memberships_org_employee",
            "organization_id",
            "employee_id",
            unique=True,
            postgresql_where=text("employee_id IS NOT NULL"),
        ),
    )


# --------------------------------------------------------------------- authorization


class Permission(Base):
    """One entry in the platform catalogue: `product.module.resource.action`.

    Global rather than per organization — the catalogue is the platform's vocabulary, and
    a customer-invented code could not be enforced by any service. Products register their
    own fragment at deploy.
    """

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    product: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    resource: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    default_scope: Mapped[str] = mapped_column(String(10), nullable=False, default="OWN")
    #: The widest scope this may ever be granted at. A role editor will not offer more,
    #: which is how "approve your own expenses, at ORG" stays impossible.
    max_scope: Mapped[str] = mapped_column(String(10), nullable=False, default="ORG")
    #: Requires recent MFA: payroll approval, PII unmask, product activation.
    is_dangerous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    route_path: Mapped[str | None] = mapped_column(String(200))
    #: Retired, never deleted. Deleting cascades through grants and silently revokes
    #: access with no audit trail; retirement is reversible.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("product", "module", "resource", "action", name="uq_permissions_tuple"),
    )


class Role(Base):
    """A named bundle of grants. Always belongs to exactly one organization.

    `organization_id` used to be nullable, with NULL meaning a platform template. Nothing
    ever created such a row — both construction sites set an organization — and templates
    already live somewhere better: `RoleTemplate` in `catalogue.py` is a **code** construct
    that `install_registration` instantiates as an ordinary org-owned row. Templates are
    code; instances are rows.

    So the nullable column was a second, unused mechanism for the same idea, and its only
    live effect would have been to make the tenancy policy subtly wrong: a policy comparing
    `organization_id` against the current tenant excludes NULL, so every template would
    vanish and a new organization would silently get no roles at all. Removed rather than
    special-cased (ADR-0002, decision 1).
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    product: Mapped[str | None] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: Assigned automatically when someone joins.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: A customer may clone a system role but not edit it.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    grants: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # A plain unique constraint now that the column cannot be NULL — the partial index
        # existed only to exclude the template rows that no longer exist.
        UniqueConstraint("organization_id", "code", name="uq_roles_org_code"),
    )


class RolePermission(Base):
    """A permission granted to a role, **at a scope**.

    The scope is the security boundary, not the name. `hr.profile.employee.write` at OWN
    is self-service; the same name at ORG is administration.
    """

    __tablename__ = "role_permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="OWN")

    role: Mapped[Role] = relationship(back_populates="grants")
    permission: Mapped[Permission] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_perm"),
    )


class MembershipRole(Base):
    """A role held by a membership. Roles attach per organization, never to a user."""

    __tablename__ = "membership_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    membership: Mapped[Membership] = relationship(back_populates="roles")
    role: Mapped[Role] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("membership_id", "role_id", name="uq_membership_roles_pair"),
    )


class Invitation(Base):
    """An outstanding invitation to join an organization.

    Only the hash of the token is stored. A database leak must not hand out working
    invitations; the raw value exists only in the email that carried it.
    """

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    product_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # One live invitation per address per organization; re-inviting resends rather
        # than accumulating tokens that all still work.
        Index(
            "uq_invitations_org_email_live",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )


# --------------------------------------------------------------------- platform tables


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
