"""Writes for the account service.

Every function here follows the same shape: change the row, record the audit entry, stage
the event — **all inside one transaction**. That is the whole reason `get_session` does not
commit for you. A crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline. Publishing before the commit is
durable is the bug the outbox exists to prevent.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import dns_check, selectors
from app import domain as domain_module
from app.catalogue import CORE_REGISTRATION, ProductManifest, ProductRegistration, min_scope
from app.models import (
    AuditLog,
    Invitation,
    Location,
    Membership,
    MembershipRole,
    Organization,
    OrgDomain,
    OrgUnit,
    OutboxEvent,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.schemas import (
    DomainIn,
    GrantIn,
    InvitationIn,
    LocationIn,
    LocationPatch,
    MemberPatch,
    OrganizationIn,
    OrganizationPatch,
    OrgUnitIn,
    OrgUnitPatch,
    RoleIn,
    SignupIn,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.cache import bump_permission_version
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event
from platform_core.logging import get_logger
from platform_core.models import utcnow

log = get_logger("account.services")

# Event names. `core.*` because these are the platform's own, not a product's.
EVT_ORG_CREATED = "core.org.created"
EVT_ORG_UPDATED = "core.org.updated"
EVT_UNIT_CREATED = "core.org_unit.created"
EVT_UNIT_UPDATED = "core.org_unit.updated"
EVT_LOCATION_CREATED = "core.location.created"
EVT_LOCATION_UPDATED = "core.location.updated"
EVT_USER_CREATED = "core.user.created"
EVT_MEMBERSHIP_CREATED = "core.membership.created"
EVT_MEMBERSHIP_UPDATED = "core.membership.updated"
EVT_MEMBERSHIP_REVOKED = "core.membership.revoked"
EVT_GRANTS_CHANGED = "core.grants.changed"

#: The scope ladder, shared by every place that has to decide whether one grant is wider
#: than another: replacing a role's grants (`_replace_grants`) and reconciling a role
#: template against a live tenant (`install_registration`, ADR-0007 §1).
_SCOPE_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id),
    }


async def _invalidate_grants(organization_id: uuid.UUID) -> None:
    """Make a grant change take effect on the next request, not at token expiry.

    Every access token carries a `pv` claim; the gateway compares it against this counter
    and refuses anything older. Without the bump that check can never fire, so a revoked
    role stays usable for the remaining life of the token — precisely the behaviour
    ADR-0000 committed to preventing.

    Called inside the transaction rather than after it commits. If the transaction then
    rolls back, the counter has still moved: that costs the organization one round of
    token refreshes and grants nobody anything, which is the direction it is safe to be
    wrong in. Deferring it to a consumer of `core.grants.changed` would be tidier and
    would leave a window in which the old token still works, which is not.
    """
    await bump_permission_version(str(organization_id))


# --------------------------------------------------------------------- organizations


async def create_organization(
    session: AsyncSession,
    payload: OrganizationIn,
    *,
    actor_user_id: uuid.UUID | None,
    request: Request | None = None,
    organization_id: uuid.UUID | None = None,
) -> Organization:
    """Create a customer.

    Not scoped by an organization — this is the one write that brings one into existence,
    so it is guarded by platform staff rather than by `require(...)`.

    `organization_id` lets sign-up (ADR-0013 §3) name the id *before* the row exists, so the
    transaction can be bound to it and the write can run on the app role under the ordinary
    policy. Every other caller leaves it None and runs as the admin role.
    """
    if await selectors.slug_taken(session, payload.slug):
        raise ConflictProblem(f"The address {payload.slug!r} is already taken")

    org = Organization(
        **payload.model_dump(exclude_none=True),
        status="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=14),
        created_by=str(actor_user_id) if actor_user_id else "system",
    )
    if organization_id is not None:
        org.id = organization_id
    session.add(org)
    await session.flush()

    # A new organization gets the core role templates immediately, so its first member
    # has something to hold.
    await install_registration(session, org.id, CORE_REGISTRATION)

    record_audit(
        session,
        AuditLog,
        organization_id=org.id,
        actor_id=actor_user_id,
        action="org.created",
        entity="organization",
        entity_id=org.id,
        after={"slug": org.slug, "name": org.name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org.id,
        event_type=EVT_ORG_CREATED,
        aggregate_type="organization",
        aggregate_id=org.id,
        payload={
            "organization_id": str(org.id),
            "slug": org.slug,
            "name": org.name,
            "timezone": org.timezone,
            "currency": org.currency,
            "country": org.country,
            "status": org.status,
        },
    )
    return org


async def update_organization(
    session: AsyncSession,
    ctx: ScopeContext,
    org: Organization,
    payload: OrganizationPatch,
    *,
    request: Request | None = None,
) -> Organization:
    before = org.as_dict(exclude={"security_policy"})
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for attr, value in changes.items():
        setattr(org, attr, value)
    org.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=org.id,
        actor_id=ctx.principal.sub,
        action="org.updated",
        entity="organization",
        entity_id=org.id,
        before=before,
        after=org.as_dict(exclude={"security_policy"}),
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org.id,
        event_type=EVT_ORG_UPDATED,
        aggregate_type="organization",
        aggregate_id=org.id,
        payload={"organization_id": str(org.id), "changed": sorted(changes)},
        actor=_actor(ctx),
    )
    return org


# --------------------------------------------------------------------- structure


async def signup(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    payload: SignupIn,
    request: Request | None = None,
) -> Membership:
    """A stranger becomes an organisation's owner (ADR-0013 §3).

    Runs inside a transaction already bound to `organization_id` — the id the route generated
    before opening it — on the ordinary app role. The organisation is created *with* that id,
    so every row this writes (organisation, core roles, user, membership, audit, outbox) passes
    the tenant policy's `WITH CHECK`, and no other organisation's rows are reachable at all.

    `ORG_OWNER` is held *and* `is_owner` is set: holding the role is what gates role
    administration, `is_owner` is what product activation's "grant the admin role to the
    owners" selects on. A membership with one and not the other is the demo-seed defect
    `tools/demo/promote_owner.py` exists to repair, and it is not repeated here.

    No credential is created. The owner's password arrives through the reset-link path once
    the address has proved it can receive mail.
    """
    org = await create_organization(
        session,
        OrganizationIn(name=payload.organization_name, slug=payload.slug),
        actor_user_id=None,
        request=request,
        organization_id=organization_id,
    )
    membership = await add_member(
        session,
        org_id=org.id,
        email=payload.email,
        full_name=payload.full_name,
        actor_user_id=None,
        role_codes=["ORG_OWNER"],
        request=request,
    )
    membership.is_owner = True
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=org.id,
        actor_id=membership.user_id,
        action="org.signed_up",
        entity="organization",
        entity_id=org.id,
        after={"slug": org.slug, "owner_email": payload.email},
        request=request,
    )
    return membership


async def create_org_unit(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: OrgUnitIn,
    *,
    request: Request | None = None,
) -> OrgUnit:
    if payload.parent_id is not None:
        parent = await selectors.get_org_unit(session, ctx.organization_id, payload.parent_id)
        if parent is None:
            raise NotFoundProblem("That parent department does not exist")

    unit = OrgUnit(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        **payload.model_dump(),
    )
    session.add(unit)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="org_unit.created",
        entity="org_unit",
        entity_id=unit.id,
        after={"code": unit.code, "name": unit.name, "parent_id": str(unit.parent_id or "")},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_UNIT_CREATED,
        aggregate_type="org_unit",
        aggregate_id=unit.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "org_unit_id": str(unit.id),
            "code": unit.code,
            "name": unit.name,
            "parent_id": str(unit.parent_id) if unit.parent_id else None,
        },
        actor=_actor(ctx),
    )
    return unit


async def move_org_unit(
    session: AsyncSession,
    ctx: ScopeContext,
    unit: OrgUnit,
    new_parent_id: uuid.UUID | None,
    *,
    request: Request | None = None,
) -> OrgUnit:
    """Re-parent a department, refusing to create a cycle.

    A cycle makes the tree unrenderable and any descendant walk non-terminating. Checking
    costs one query; discovering it in production costs an outage.
    """
    if new_parent_id == unit.id:
        raise BusinessRuleProblem("A department cannot report to itself")

    if new_parent_id is not None:
        parent = await selectors.get_org_unit(session, ctx.organization_id, new_parent_id)
        if parent is None:
            raise NotFoundProblem("That parent department does not exist")
        if new_parent_id in await selectors.descendant_ids(session, ctx.organization_id, unit.id):
            raise BusinessRuleProblem(
                "That would put the department underneath one of its own sub-departments"
            )

    before = {"parent_id": str(unit.parent_id or "")}
    unit.parent_id = new_parent_id
    unit.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="org_unit.moved",
        entity="org_unit",
        entity_id=unit.id,
        before=before,
        after={"parent_id": str(new_parent_id or "")},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_UNIT_UPDATED,
        aggregate_type="org_unit",
        aggregate_id=unit.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "org_unit_id": str(unit.id),
            "parent_id": str(new_parent_id) if new_parent_id else None,
        },
        actor=_actor(ctx),
    )
    return unit


async def update_org_unit(
    session: AsyncSession,
    ctx: ScopeContext,
    unit: OrgUnit,
    payload: OrgUnitPatch,
    *,
    request: Request | None = None,
) -> OrgUnit:
    """Rename, re-lead or deactivate a department. `code` is never touched — it stays out
    of `changes` because `OrgUnitPatch` does not carry it at all.
    """
    changes = payload.model_dump(exclude_unset=True)

    new_head = changes.get("head_membership_id")
    if "head_membership_id" in changes and new_head is not None:
        head = await selectors.get_member(session, ctx.organization_id, new_head)
        if head is None:
            raise NotFoundProblem("That member does not exist")

    if changes.get("is_active") is False and unit.is_active:
        if await selectors.org_unit_has_active_children(session, ctx.organization_id, unit.id):
            raise BusinessRuleProblem(
                "This department still has active sub-departments. Move or deactivate them first."
            )
        if await selectors.org_unit_has_active_members(session, ctx.organization_id, unit.id):
            raise BusinessRuleProblem(
                "This department still has members assigned to it. Move them first."
            )

    before = {
        "name": unit.name,
        "kind": unit.kind,
        "head_membership_id": str(unit.head_membership_id or ""),
        "is_active": unit.is_active,
    }
    for attr, value in changes.items():
        setattr(unit, attr, value)
    unit.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="org_unit.updated",
        entity="org_unit",
        entity_id=unit.id,
        before=before,
        after={
            "name": unit.name,
            "kind": unit.kind,
            "head_membership_id": str(unit.head_membership_id or ""),
            "is_active": unit.is_active,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_UNIT_UPDATED,
        aggregate_type="org_unit",
        aggregate_id=unit.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "org_unit_id": str(unit.id),
            "is_active": unit.is_active,
        },
        actor=_actor(ctx),
    )
    return unit


async def create_location(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: LocationIn,
    *,
    request: Request | None = None,
) -> Location:
    location = Location(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        **payload.model_dump(),
    )
    session.add(location)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="location.created",
        entity="location",
        entity_id=location.id,
        after={"code": location.code, "name": location.name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LOCATION_CREATED,
        aggregate_type="location",
        aggregate_id=location.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "location_id": str(location.id),
            "code": location.code,
            "name": location.name,
            "timezone": location.timezone,
        },
        actor=_actor(ctx),
    )
    return location


async def update_location(
    session: AsyncSession,
    ctx: ScopeContext,
    location: Location,
    payload: LocationPatch,
    *,
    request: Request | None = None,
) -> Location:
    """Rename, re-site or deactivate a site. `code` stays out of `LocationPatch` entirely —
    it is embedded in members' `location_codes` and in tokens.
    """
    before = {
        "name": location.name,
        "timezone": location.timezone,
        "country": location.country,
        "is_active": location.is_active,
    }
    changes = payload.model_dump(exclude_unset=True)
    for attr, value in changes.items():
        setattr(location, attr, value)
    location.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="location.updated",
        entity="location",
        entity_id=location.id,
        before=before,
        after={
            "name": location.name,
            "timezone": location.timezone,
            "country": location.country,
            "is_active": location.is_active,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LOCATION_UPDATED,
        aggregate_type="location",
        aggregate_id=location.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "location_id": str(location.id),
            "is_active": location.is_active,
        },
        actor=_actor(ctx),
    )
    return location


# --------------------------------------------------------------------- members


async def _default_role_ids(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    roles = await selectors.list_roles(session, org_id)
    return [r.id for r in roles if r.is_default and r.organization_id == org_id]


async def add_member(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    email: str,
    full_name: str,
    actor_user_id: uuid.UUID | None,
    role_codes: list[str] | None = None,
    request: Request | None = None,
) -> Membership:
    """Attach a person to an organization, creating the platform user if new.

    The user is looked up by email **across the whole platform**: somebody who already
    works for another customer gets a second membership, not a second account. That is the
    single-identity promise, and it is why this reads before it writes.
    """
    user = await selectors.get_user_by_email(session, email)
    created_user = user is None
    if user is None:
        user = User(email=email, full_name=full_name)
        session.add(user)
        await session.flush()
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_USER_CREATED,
            aggregate_type="user",
            aggregate_id=user.id,
            payload={"user_id": str(user.id), "email": user.email, "full_name": user.full_name},
        )

    existing = await selectors.get_membership_for_user(session, org_id, user.id)
    if existing is not None and existing.status != "removed":
        raise ConflictProblem("That person is already a member of this organization")

    membership = existing or Membership(organization_id=org_id, user_id=user.id)
    membership.status = "active"
    membership.joined_at = datetime.now(UTC)
    membership.invited_by = actor_user_id
    session.add(membership)
    await session.flush()

    wanted = list(role_codes or [])
    role_ids: list[uuid.UUID] = []
    for code in wanted:
        role = await selectors.get_role_by_code(session, org_id, code)
        if role is None:
            raise NotFoundProblem(f"No role named {code!r} in this organization")
        role_ids.append(role.id)
    if not role_ids:
        role_ids = await _default_role_ids(session, org_id)

    for role_id in role_ids:
        session.add(
            MembershipRole(membership_id=membership.id, role_id=role_id, granted_by=actor_user_id)
        )
    # Load the relationship now. A lazy load after the transaction closes raises
    # MissingGreenlet, and the caller has no way to know it needed to ask.
    await session.flush()
    await session.refresh(membership, ["roles"])

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=actor_user_id,
        action="membership.created",
        entity="membership",
        entity_id=membership.id,
        after={"email": user.email, "roles": wanted, "user_created": created_user},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_MEMBERSHIP_CREATED,
        aggregate_type="membership",
        aggregate_id=membership.id,
        payload={
            "organization_id": str(org_id),
            "membership_id": str(membership.id),
            "user_id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
        },
    )
    return membership


#: The partial unique index on `(organization_id, employee_id)` — one employee, one
#: membership, per organization (`models.Membership.__table_args__`). Named here because
#: ADR-0008 §5 requires the *specific* constraint to be recognised rather than every
#: integrity failure being reported as this one business rule.
EMPLOYEE_LINK_CONSTRAINT = "uq_memberships_org_employee"


def _integrity_error_constraint(exc: IntegrityError) -> str | None:
    """The name of the constraint that actually fired, when the driver surfaces one — or
    `None` when it does not (a constraint with no name, or an `IntegrityError` that is not a
    constraint violation at all).

    asyncpg's own exception carries `constraint_name` directly, but SQLAlchemy's asyncpg
    dialect re-raises it as a driver-shaped exception first
    (`AsyncAdapt_asyncpg_dbapi.IntegrityError`, raised `from` the original), so the
    structured attribute survives on `exc.orig.__cause__` rather than on `exc.orig` itself.
    Both are checked, in that order, so this keeps working if a future SQLAlchemy version
    stops wrapping. The same helper `approval/app/services.py` uses for its exclusion
    constraint, for the same reason.
    """
    orig = exc.orig
    name = getattr(orig, "constraint_name", None)
    if name is not None:
        return str(name)
    cause = getattr(orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    return str(name) if name is not None else None


def _employee_already_linked(
    employee_id: uuid.UUID, holder_membership_id: uuid.UUID | None
) -> ConflictProblem:
    """ADR-0008 §5's 409, naming the membership that holds the link.

    Naming it reveals nothing the caller cannot already list — reaching this code needs
    `core.identity.membership.write` at ORG — and it is the difference between an error and
    an instruction: the remedy is to PATCH that membership with `employee_id: null` first.
    **Nothing is auto-unlinked**; reassigning somebody's identity as a side effect of
    somebody else's PATCH is not a fix.
    """
    return ConflictProblem(
        "That employee is already linked to another member of this organization",
        errors=[
            {
                "field": "employee_id",
                "code": "employee_already_linked",
                "message": (
                    "That employee record is already linked to another member. Unlink them "
                    "first (PATCH that member with employee_id: null), then link this one."
                ),
                "employee_id": str(employee_id),
                "membership_id": str(holder_membership_id) if holder_membership_id else None,
            }
        ],
    )


async def _write_employee_link(
    session: AsyncSession,
    org_id: uuid.UUID,
    membership: Membership,
    employee_id: uuid.UUID | None,
) -> None:
    """Write the link, translating **only** `uq_memberships_org_employee` into a 409.

    The pre-check in `update_member` is what makes the common case legible; this is what
    makes it correct. Between that read and this write another transaction can link the same
    employee, and the index is what refuses it — ADR-0008 §5 requires both, and neither alone
    is sufficient.

    Four details are load-bearing, and the first two were arrived at the hard way:

    * The write is an explicit tenant-scoped `UPDATE` inside a **savepoint**, not an ORM
      `flush()`. A failed flush marks the enclosing `Session` transaction as rolled back, so
      nothing may be executed on it afterwards — including the one question this function
      still has to ask. A statement-level failure inside a savepoint leaves the transaction
      usable, which is what lets the raced path name the holder instead of shrugging.
    * The statement names `organization_id` as well as the primary key. Under RLS the policy
      would refuse another tenant's row anyway; saying it here means the query is correct
      before the net is, which is the rule for every scoped query in this service.
    * Anything other than the link constraint **re-raises** and reaches
      `platform_core.errors`' generic, non-leaking handler. A broad `except IntegrityError`
      here would report every integrity failure as this one business rule, which for anything
      else is simply false — the bug the delegation slice shipped and had to fix.
    * `no_autoflush` keeps the rest of the PATCH's pending attribute changes out of this
      probe: they are flushed by the caller's commit, and dragging them into a savepoint that
      exists to test one constraint is how an unrelated failure arrives wearing this one's
      exception.

    The ORM attribute is set only after the statement succeeds, so an object in the identity
    map never claims a link the database refused.
    """
    stmt = (
        update(Membership)
        .where(Membership.id == membership.id, Membership.organization_id == org_id)
        .values(employee_id=employee_id)
        .execution_options(synchronize_session=False)
    )
    try:
        with session.no_autoflush:
            async with session.begin_nested():
                await session.execute(stmt)
    except IntegrityError as exc:
        if _integrity_error_constraint(exc) != EMPLOYEE_LINK_CONSTRAINT:
            raise
        assert employee_id is not None  # clearing a link cannot violate this index
        holder = await selectors.get_membership_by_employee(session, org_id, employee_id)
        holder_id = holder.id if holder is not None and holder.id != membership.id else None
        raise _employee_already_linked(employee_id, holder_id) from exc
    membership.employee_id = employee_id


async def update_member(
    session: AsyncSession,
    ctx: ScopeContext,
    membership: Membership,
    payload: MemberPatch,
    *,
    request: Request | None = None,
) -> Membership:
    """Change one membership's facts, including **who that principal is** (ADR-0008 §2).

    `employee_id` is handled explicitly and popped out of `changes` before the blind
    `setattr` loop, exactly as `location_codes` already is. Letting the loop reach it would
    write the sole origin of the `sref` claim with no conflict handling and no old value in
    the audit row — which is how a 23505 becomes a 500 and how an identity reassignment
    leaves no trail.
    """
    changes = payload.model_dump(exclude_unset=True)
    # Captured before anything is popped: the event names every field the caller actually
    # sent, including the ones handled explicitly below.
    changed_fields = sorted(changes)

    # --- the identity link (ADR-0008) -------------------------------------------------
    # Popped first so the `setattr` loop below cannot reach it under any later edit.
    employee_id_requested = "employee_id" in changes
    new_employee_id: uuid.UUID | None = changes.pop("employee_id", None)
    previous_employee_id = membership.employee_id
    employee_link_changes = employee_id_requested and new_employee_id != previous_employee_id

    if employee_link_changes and new_employee_id is not None:
        # Opportunistic, not the enforcement (`_write_employee_link` is): reads before the
        # write so the ordinary, non-racing case gets a 409 that names the holder.
        holder = await selectors.get_membership_by_employee(
            session, ctx.organization_id, new_employee_id
        )
        if holder is not None and holder.id != membership.id:
            raise _employee_already_linked(new_employee_id, holder.id)

    if "manager_membership_id" in changes:
        manager_id = changes["manager_membership_id"]
        if manager_id == membership.id:
            raise BusinessRuleProblem("Somebody cannot report to themselves")
        if manager_id is not None:
            manager = await selectors.get_member(session, ctx.organization_id, manager_id)
            if manager is None:
                raise NotFoundProblem("That manager is not a member of this organization")

    if "org_unit_id" in changes and changes["org_unit_id"] is not None:
        unit = await selectors.get_org_unit(session, ctx.organization_id, changes["org_unit_id"])
        if unit is None:
            raise NotFoundProblem("That department does not exist")

    before = {
        "org_unit_id": str(membership.org_unit_id or ""),
        "manager_membership_id": str(membership.manager_membership_id or ""),
        "location_codes": membership.location_codes,
        "job_title": membership.job_title,
        # ADR-0008 §10: an identity reassignment that is not in the audit trail is not
        # defensible. One action code, so a member's whole history reads in one vocabulary.
        "employee_id": str(previous_employee_id or ""),
    }

    if "location_codes" in changes and changes["location_codes"] is not None:
        membership.location_codes = ",".join(changes.pop("location_codes"))
    changes.pop("location_codes", None)
    for attr, value in changes.items():
        setattr(membership, attr, value)

    if employee_link_changes:
        await _write_employee_link(session, ctx.organization_id, membership, new_employee_id)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="membership.updated",
        entity="membership",
        entity_id=membership.id,
        before=before,
        after={
            "org_unit_id": str(membership.org_unit_id or ""),
            "manager_membership_id": str(membership.manager_membership_id or ""),
            "location_codes": membership.location_codes,
            "job_title": membership.job_title,
            "employee_id": str(membership.employee_id or ""),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MEMBERSHIP_UPDATED,
        aggregate_type="membership",
        aggregate_id=membership.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "membership_id": str(membership.id),
            "changed": changed_fields,
        },
        actor=_actor(ctx),
    )
    # ADR-0008 §7's asymmetry, and it is deliberate. `pv` is bumped **per organization**, so
    # every bump is a tenant-wide token-refresh burst:
    #
    #   null -> an employee id   no bump. The routine onboarding path. The token catches up
    #                            at the next refresh, within one 900 s access-token life —
    #                            ADR-0000 §4's "tolerable for a grant". Bumping here would
    #                            fire that burst several times a day in a 4,000-person tenant.
    #   a link cleared/replaced  bump. This is the revocation shape — the operation that
    #                            *stops* somebody acting as an employee, most importantly to
    #                            correct a wrong link — and fifteen more minutes of the wrong
    #                            identity after the fix is not an acceptable answer.
    employee_link_revoked = employee_link_changes and previous_employee_id is not None

    # Locations, department and reporting line all feed scope resolution, and `loc` is
    # carried in the token — so a move between sites has to invalidate it too. A change
    # to job title or display name does not.
    if (
        before["location_codes"] != membership.location_codes
        or before["org_unit_id"] != str(membership.org_unit_id or "")
        or before["manager_membership_id"] != str(membership.manager_membership_id or "")
        or employee_link_revoked
    ):
        await _invalidate_grants(ctx.organization_id)
    return membership


async def revoke_member(
    session: AsyncSession,
    ctx: ScopeContext,
    membership: Membership,
    *,
    reason: str | None = None,
    request: Request | None = None,
) -> Membership:
    """Remove somebody from an organization.

    Their **platform account survives** — they may work for another customer, and even if
    not, deleting the user would orphan every record that references them. Access ends;
    history does not.
    """
    if membership.is_owner:
        raise BusinessRuleProblem(
            "The owner cannot be removed. Transfer ownership first, so the account is "
            "never left with nobody who can administer it."
        )

    membership.status = "removed"
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="membership.revoked",
        entity="membership",
        entity_id=membership.id,
        before={"status": "active"},
        after={"status": "removed"},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MEMBERSHIP_REVOKED,
        aggregate_type="membership",
        aggregate_id=membership.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "membership_id": str(membership.id),
            "user_id": str(membership.user_id),
        },
        actor=_actor(ctx),
    )
    # The most important of the four bumps: without it, somebody removed from the
    # organization keeps working until their access token expires.
    await _invalidate_grants(ctx.organization_id)
    return membership


async def _caller_grant_ceiling(session: AsyncSession, ctx: ScopeContext) -> dict[str, str]:
    """The caller's own resolved grants, as a `{code: widest_scope}` map.

    The single source every "can this caller hand out this code at this scope" check in
    the service reads from — assigning a role (`_resolve_assignable_roles`, used by
    `invite` and `set_member_roles`) and writing a role's own grants (`_replace_grants`,
    used by `create_role` and `replace_role_grants`). One helper, so there is exactly one
    definition of the ceiling rather than two that can drift apart.

    Read via `selectors.resolve_grants` against the caller's **own** membership — a
    database read of their actual role assignments, not `ctx.principal.perms`. The token's
    perms claim is exactly what the caller presented to get here and is refreshed on the
    gateway's cadence, not this request's; the roles a person actually holds right now is
    the question being asked, and the database is the one place that can answer it without
    trusting the same credential the whole check exists to look past.

    No `ORG_OWNER` special case: the role is `CORE.all("ORG")` plus every activated
    product's admin role granted on activation, so resolving the owner's real grants
    already covers "the owner can build any role" — deriving it from a role code would be
    a second, looser definition of the same thing.
    """
    if ctx.principal.membership_id is None:
        raise ForbiddenProblem("No membership to grant permissions from")
    caller_grants = await selectors.resolve_grants(session, ctx.principal.membership_id)
    return dict(grant.split(":", 1) for grant in caller_grants)


def _caller_holds(ceiling: dict[str, str], code: str, scope: str) -> bool:
    """Whether the caller's own ceiling covers `code` at `scope` or wider."""
    held = ceiling.get(code)
    return held is not None and _SCOPE_RANK[held] >= _SCOPE_RANK[scope]


async def _resolve_assignable_roles(
    session: AsyncSession, ctx: ScopeContext, role_codes: list[str]
) -> list[Role]:
    """Resolve `role_codes`, refusing any the caller could not grant themselves.

    Shared by `invite` and `set_member_roles` — the two places somebody hands another
    person a role. Checking only that the role *exists* (the previous shape of both) let a
    caller who merely holds `identity.membership.write` invite, or promote, an address they
    control into a role that outranks them: an `ORG_ADMIN` naming `ORG_OWNER` in
    `role_codes` was accepted with nothing else checked. Every grant the target role carries
    now has to be something the caller already holds themselves, at that scope or wider.
    """
    resolved: list[Role] = []
    for code in role_codes:
        role = await selectors.get_role_by_code(session, ctx.organization_id, code)
        if role is None:
            raise NotFoundProblem(f"No role named {code!r} in this organization")
        resolved.append(role)

    if not resolved:
        return resolved

    ceiling = await _caller_grant_ceiling(session, ctx)
    for role in resolved:
        for grant in role.grants:
            code = grant.permission.code
            if not _caller_holds(ceiling, code, grant.scope):
                raise ForbiddenProblem(
                    f"You cannot assign {role.code!r}: it grants {code} at {grant.scope}, "
                    "which you do not hold yourself at that scope or wider."
                )
    return resolved


async def set_member_roles(
    session: AsyncSession,
    ctx: ScopeContext,
    membership: Membership,
    role_codes: list[str],
    *,
    request: Request | None = None,
) -> Membership:
    """Replace a member's roles, and bump the permission version.

    The bump is what makes a revocation take effect on the next request rather than when
    the access token happens to expire.
    """
    before = sorted(mr.role.code for mr in membership.roles)

    resolved = await _resolve_assignable_roles(session, ctx, role_codes)

    await session.execute(
        delete(MembershipRole).where(MembershipRole.membership_id == membership.id)
    )
    for role in resolved:
        session.add(
            MembershipRole(
                membership_id=membership.id, role_id=role.id, granted_by=ctx.principal.sub
            )
        )
    await session.flush()
    await session.refresh(membership, ["roles"])

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="membership.roles_changed",
        entity="membership",
        entity_id=membership.id,
        before={"roles": before},
        after={"roles": sorted(role_codes)},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_GRANTS_CHANGED,
        aggregate_type="membership",
        aggregate_id=membership.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "membership_id": str(membership.id),
            "user_id": str(membership.user_id),
            "roles": sorted(role_codes),
        },
        actor=_actor(ctx),
    )
    await _invalidate_grants(ctx.organization_id)
    return membership


# --------------------------------------------------------------------- roles


async def create_role(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: RoleIn,
    *,
    request: Request | None = None,
) -> Role:
    if await selectors.get_role_by_code(session, ctx.organization_id, payload.code):
        raise ConflictProblem(f"A role named {payload.code!r} already exists")

    role = Role(
        organization_id=ctx.organization_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        product=payload.product,
    )
    session.add(role)
    await session.flush()
    await _replace_grants(session, ctx, role, payload.grants)
    await session.refresh(role, ["grants"])

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="role.created",
        entity="role",
        entity_id=role.id,
        after={"code": role.code, "grants": [g.permission_code for g in payload.grants]},
        request=request,
    )
    return role


async def replace_role_grants(
    session: AsyncSession,
    ctx: ScopeContext,
    role: Role,
    payload: RoleIn,
    *,
    request: Request | None = None,
) -> Role:
    """Replace a role's grants, and apply its name and description.

    `payload.code` is never read: a role's code is how invitations and role lookups name
    it, and it stays immutable for the life of the role. The route schema still requires
    the field — the caller sends it back unchanged — but this is where "unchanged" is
    enforced, by simply never looking at it.
    """
    if role.is_system:
        raise BusinessRuleProblem(
            "A system role cannot be edited. Clone it and change the copy, so the "
            "platform's defaults stay recognisable."
        )
    before = {
        "name": role.name,
        "description": role.description,
        "grants": sorted(f"{g.permission.code}:{g.scope}" for g in role.grants),
    }
    role.name = payload.name
    role.description = payload.description
    await _replace_grants(session, ctx, role, payload.grants)
    await session.refresh(role, ["grants"])

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="role.grants_changed",
        entity="role",
        entity_id=role.id,
        before=before,
        after={
            "name": role.name,
            "description": role.description,
            "grants": sorted(f"{g.permission_code}:{g.scope}" for g in payload.grants),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_GRANTS_CHANGED,
        aggregate_type="role",
        aggregate_id=role.id,
        payload={"organization_id": str(ctx.organization_id), "role_id": str(role.id)},
        actor=_actor(ctx),
    )
    # Editing a role changes what everybody holding it may do, so this bump invalidates
    # far more tokens than a single membership change — which is exactly right.
    await _invalidate_grants(ctx.organization_id)
    return role


async def delete_role(
    session: AsyncSession,
    ctx: ScopeContext,
    role: Role,
    *,
    request: Request | None = None,
) -> None:
    """Delete a custom role, refusing while it is still in use.

    Not `ON DELETE CASCADE`: `membership_roles.role_id` cascades, so deleting a role a
    membership still holds would silently strip that person's access with no audit line
    explaining why. Checked here instead, and refused with 409 while either a membership or
    a pending invitation still names it.
    """
    if role.is_system:
        raise BusinessRuleProblem("A system role cannot be deleted.")
    if await selectors.role_held_by_a_membership(session, ctx.organization_id, role.id):
        raise ConflictProblem(
            "This role is still held by at least one member. Reassign them before deleting it."
        )
    pending = await selectors.role_named_by_a_pending_invitation(
        session, ctx.organization_id, role.code
    )
    if pending:
        raise ConflictProblem(
            "This role is named on a pending invitation. Revoke it, or wait for it to expire, "
            "before deleting the role."
        )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="role.deleted",
        entity="role",
        entity_id=role.id,
        before={"code": role.code, "name": role.name},
        request=request,
    )
    await session.delete(role)
    await _invalidate_grants(ctx.organization_id)


async def _replace_grants(
    session: AsyncSession, ctx: ScopeContext, role: Role, grants: list[GrantIn]
) -> None:
    """Set a role's grants, refusing anything wider than the permission allows, or that
    the caller does not hold themselves.

    `max_scope` is the reason a role editor cannot quietly turn a self-service permission
    into an organization-wide one. The ceiling check below is the reason a holder of
    `role.write` alone — who can edit any non-system role, including one they already
    hold — cannot use that edit to hand the role a permission `_resolve_assignable_roles`
    would have refused them for the same role via `invite` or `set_member_roles`. Editing
    a role they cannot yet fully act as is otherwise indistinguishable from assigning
    themselves a role they cannot hold: both end with the caller's own effective grants
    wider than before the call.
    """
    ceiling = await _caller_grant_ceiling(session, ctx)

    # An explicit DELETE rather than mutating the collection: touching `role.grants` on a
    # freshly flushed object triggers a lazy load, which raises MissingGreenlet under
    # asyncio. It is also one statement instead of N.
    await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))

    for grant in grants:
        permission = await selectors.get_permission_by_code(session, grant.permission_code)
        if permission is None:
            raise NotFoundProblem(f"No permission named {grant.permission_code!r}")
        if _SCOPE_RANK[grant.scope] > _SCOPE_RANK[permission.max_scope]:
            raise BusinessRuleProblem(
                f"{permission.code} cannot be granted at {grant.scope}; "
                f"the widest it supports is {permission.max_scope}"
            )
        if not _caller_holds(ceiling, permission.code, grant.scope):
            raise ForbiddenProblem(
                f"You cannot grant {permission.code} at {grant.scope}: you do not hold "
                "it yourself at that scope or wider."
            )
        session.add(RolePermission(role_id=role.id, permission_id=permission.id, scope=grant.scope))


# --------------------------------------------------------------------- domains
#
# ADR-0000 §1: a verified domain selects an identity *provider*, never an organization, and
# only once DNS has proved it.
#
# **Exclusivity is verified-only** (`OrgDomain`'s own docstring in `models.py` has the full
# argument). `uq_org_domains_org_domain` — `UNIQUE (organization_id, domain)` — is the only
# thing stopping one organization from claiming the same domain twice; it says nothing about
# any other organization. `uq_org_domains_domain_verified` — a partial `UNIQUE (domain) WHERE
# verified_at IS NOT NULL` — is the actual lock, and it is what makes takeover safety a
# database guarantee that needs **no cross-tenant read or write anywhere in this module**:
#
# * **Claiming** never has anything to collide with across organizations any more. Every
#   organization's own row is unique to itself (the per-org constraint), and a pending claim
#   proves nothing, so there is nothing for a second organization's pending claim to threaten.
#   `claim_domain` therefore only ever reads and writes *this* organization's own row.
# * **Verifying** is the one moment ownership is actually asserted, and it is asserted by
#   setting `verified_at` on a row this organization already owns — an ordinary, tenant-scoped
#   `UPDATE`. If another organization's row already holds `verified_at` for the same domain,
#   the partial unique index refuses the second `UPDATE` with a constraint violation, atomically,
#   however many organizations race to verify the same domain at once. `verify_domain` below
#   just needs to catch that violation and answer with the same anonymous 409 a claim conflict
#   would — it never has to ask, or decide, who else holds the domain.
# * A domain another organization has already verified stays claimable as **pending** by anyone
#   else (harmless: `api.internal.organization_by_domain` only ever answers with a verified row,
#   so a pending one never routes SSO for anybody), and that pending claim's own verify attempt
#   is what surfaces the 409 — not the claim itself. Whether to also refuse a claim pre-emptively
#   when the domain is already verified elsewhere was deliberately left out: it would need a
#   cross-tenant read to word nicely and buys nothing verify's own guard does not already cover.
# * An abandoned pending claim needs no sweep or admin tool to stop mattering: it was never
#   dangerous to begin with (unverified never routes anything), and it lazily expires on its own
#   14-day clock (`domain.CLAIM_TTL`) the same as any single-organization claim always has.
#
# **Periodic re-verification** (`recheck_domain` below, driven by `app/cron.py`) is the other
# half of ADR-0000 §1 this module owns: proving ownership once, at claim time, says nothing
# about whether the record is still published a year later. It never revokes `verified_at` by
# itself — see `recheck_domain`'s own docstring for why — only records what it found and flags
# a domain that stopped answering, for a human to act on.

EVT_DOMAIN_CLAIMED = "core.identity.domain.claimed"
EVT_DOMAIN_VERIFIED = "core.identity.domain.verified"
EVT_DOMAIN_RELEASED = "core.identity.domain.released"
EVT_DOMAIN_CHECK_FAILED = "core.identity.domain.check_failed"
EVT_DOMAIN_CHECK_RECOVERED = "core.identity.domain.check_recovered"
EVT_DOMAIN_DEMOTED = "core.identity.domain.demoted"

#: The system actor every re-check-driven audit row and event carries — nobody is signed in
#: while a sweep runs, and `AuditLog.actor_id` stays `NULL` for exactly that reason, the same
#: shape `tools/reconcile_permissions.py`'s own audit rows use (`acting_as`, no `actor_id`).
DOMAIN_RECHECK_ACTOR = "domain-recheck-sweep"


async def claim_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: DomainIn,
    *,
    request: Request | None = None,
) -> OrgDomain:
    """Claim a domain, or re-claim one this organization already holds.

    A fresh, unverified row for a domain this organization has not claimed before; a rotated
    token and a reset clock for one it already claimed and has not yet verified; an unchanged,
    idempotent no-op for one it has already verified — there is nothing to prove twice, and
    rotating a verified domain's token would silently point a published TXT record at a value
    nobody asked to be re-verified against.

    Never blocked by, and never disclosing anything about, another organization's claim on the
    same domain — see the module docstring above. The only collision this can still hit is this
    organization double-submitting the same brand-new domain in a race with itself, which
    `uq_org_domains_org_domain` catches and this function answers with the same anonymous 409
    `verify_domain`'s cross-organization race does.

    **The re-claim's own token rotation is itself a conditional `UPDATE`, not an ORM attribute
    assignment** (round 3): `existing` was read a moment ago on a plain `SELECT`, and this
    organization's own concurrent `verify_domain` call — a second browser tab, a slow request
    retried — could complete and set `verified_at` in the gap between that read and this write.
    Without the `WHERE verified_at IS NULL` guard, this call would happily rotate the token
    *after* the domain had genuinely been verified, leaving `verified_at` set (true) but
    `verification_token` pointing at a value nobody has published — the domain would look
    verified right up until the next periodic re-check, at which point it would fail against a
    TXT record that was only ever the *old* proof, and start counting toward demotion for a
    domain that was never actually broken. Zero rows updated means exactly that race happened;
    the fix is to answer with the now-verified row instead of a token nobody can use.
    """
    normalised = domain_module.normalise_domain(payload.domain)

    existing = await selectors.get_domain_by_name(session, ctx.organization_id, normalised)
    if existing is not None:
        if existing.verified_at is not None:
            return existing
        new_token = domain_module.generate_verification_token()
        now = utcnow()
        stmt = (
            update(OrgDomain)
            .where(
                OrgDomain.id == existing.id,
                OrgDomain.organization_id == ctx.organization_id,
                OrgDomain.verified_at.is_(None),
            )
            .values(verification_token=new_token, created_at=now)
        )
        result = cast("CursorResult[Any]", await session.execute(stmt))
        if result.rowcount == 0:
            fresh = await selectors.get_domain(session, ctx.organization_id, existing.id)
            if fresh is None:
                raise NotFoundProblem("No such domain")
            return fresh
        existing.verification_token = new_token
        existing.created_at = now
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="domain.claimed",
            entity="org_domain",
            entity_id=existing.id,
            after={"domain": existing.domain, "reclaimed": True},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_DOMAIN_CLAIMED,
            aggregate_type="org_domain",
            aggregate_id=existing.id,
            payload={
                "organization_id": str(ctx.organization_id),
                "domain_id": str(existing.id),
                "domain": existing.domain,
            },
            actor=_actor(ctx),
        )
        return existing

    # **Serialised per organization, transaction-scoped** (round 3): two concurrent claims for
    # two *different* new domains from the same organization could both read the count below
    # as one under the cap and both insert, over-running `MAX_DOMAINS_PER_ORGANIZATION` by
    # however many requests raced. `pg_advisory_xact_lock` is not a security boundary — nothing
    # downstream trusts the count alone — but it is what makes the count read here still true
    # at the moment the row below is inserted. Released automatically at commit or rollback,
    # so there is nothing to remember to release and nothing held across requests or the
    # existing-domain branch above, which never changes the count and does not need it.
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"domains:{ctx.organization_id}"},
    )
    current = await selectors.list_domains(session, ctx.organization_id)
    if len(current) >= domain_module.MAX_DOMAINS_PER_ORGANIZATION:
        raise ValidationProblem(
            f"This organization has already claimed "
            f"{domain_module.MAX_DOMAINS_PER_ORGANIZATION} domains, the most it may hold at "
            "once. Release one before claiming another."
        )

    row = OrgDomain(
        organization_id=ctx.organization_id,
        domain=normalised,
        is_primary=not current,
        verification_token=domain_module.generate_verification_token(),
    )
    session.add(row)
    # Flushed inside a savepoint so a double-submitted create of the very same domain by this
    # same organization becomes a named, anonymous 409 rather than a raw `IntegrityError` —
    # the only collision `uq_org_domains_org_domain` can still raise here.
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise ConflictProblem(
            "That domain could not be claimed. It may already be in use."
        ) from exc

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="domain.claimed",
        entity="org_domain",
        entity_id=row.id,
        after={"domain": row.domain, "reclaimed": False},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DOMAIN_CLAIMED,
        aggregate_type="org_domain",
        aggregate_id=row.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "domain_id": str(row.id),
            "domain": row.domain,
        },
        actor=_actor(ctx),
    )
    return row


async def verify_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    row: OrgDomain,
    *,
    resolver: dns_check.Resolver | None = None,
    request: Request | None = None,
) -> OrgDomain:
    """Check DNS for the TXT record this claim asked for, and mark it verified if it is there.

    Idempotent once verified: a second call is a no-op rather than a fresh DNS round trip,
    because nothing about "already proved" changes by asking again.

    A claim past `domain.CLAIM_TTL` is refused with a message naming re-claiming rather than a
    bare failed check — the token published for it is treated as abandoned evidence, the same
    rule `domain.is_claim_expired` states.

    **The DNS lookup happens before any write is even attempted** — the slow, unreliable part
    of this call touches no transaction state at all, so a resolver having a bad day costs a
    read-only round trip and nothing else: no lock held, no write staged and rolled back.

    **The write is a single conditional `UPDATE ... WHERE ... RETURNING`, not an ORM attribute
    assignment.** Three things have to still be true at the exact moment this lands, not merely
    when `row` was read a DNS round trip ago: this is still this organization's row (`id` and
    `organization_id`), it is still unverified (`verified_at IS NULL`), and it is still the
    same claim the DNS answer was checked against (`verification_token` unchanged) — a
    concurrent re-claim rotates the token, and a check that passed against the *old* token must
    not silently verify the *new* one. `uq_org_domains_domain_verified` is the second guard,
    for the cross-organization race: if another organization verified the same domain first,
    the `UPDATE` itself raises a unique violation rather than matching zero rows, and that is
    caught and turned into the same anonymous 409 either race gives — no organization identity,
    no hint of which of "somebody else already holds it" or "you lost a race happening right
    now" is true. Whichever guard trips, the answer is a named `ConflictProblem` or
    `NotFoundProblem`, never an unhandled exception.
    """
    if row.verified_at is not None:
        return row
    if domain_module.is_claim_expired(row.created_at):
        raise BusinessRuleProblem(
            "This claim has expired. Claim the domain again to get a new verification code."
        )

    host = domain_module.verification_txt_host(row.domain)
    expected = domain_module.verification_txt_value(row.verification_token)
    found = await dns_check.txt_contains(host, expected, resolver=resolver)
    if not found:
        raise BusinessRuleProblem(
            f"No matching TXT record found at {host}. DNS changes can take a while to "
            "propagate — publish the record and try again shortly."
        )

    now = utcnow()
    stmt = (
        update(OrgDomain)
        .where(
            OrgDomain.id == row.id,
            OrgDomain.organization_id == ctx.organization_id,
            OrgDomain.verified_at.is_(None),
            OrgDomain.verification_token == row.verification_token,
        )
        .values(
            verified_at=now,
            # A fresh proof of ownership is a fresh start for the re-check history — carrying
            # a prior failing streak (or a prior demotion's failure count) into a domain that
            # was just re-verified would let one stale failure immediately look like part of a
            # new one. `demoted_at` is not reset: it is a permanent historical marker, not a
            # current-state flag (see its own docstring in `models.py`).
            last_checked_at=None,
            last_check_ok=None,
            failing_since=None,
            consecutive_failures=0,
        )
    )
    try:
        async with session.begin_nested():
            # `session.execute` is typed to return the generic `Result`, but an UPDATE
            # statement always produces a `CursorResult` at runtime — which is what actually
            # carries `rowcount` (the same cast `approval/app/services.py` documents).
            result = cast("CursorResult[Any]", await session.execute(stmt))
    except IntegrityError as exc:
        raise ConflictProblem(
            "That domain could not be verified. It may already be verified elsewhere."
        ) from exc

    if result.rowcount == 0:
        # Not a crash, and not ambiguous: either this row is gone (released, out from under
        # a concurrent request), or the claim changed underneath us in some way the three
        # `WHERE` conditions above did not simply satisfy — re-claimed (a new token) or
        # already verified by a duplicate concurrent call from this same organization.
        fresh = await selectors.get_domain(session, ctx.organization_id, row.id)
        if fresh is None:
            raise NotFoundProblem("No such domain")
        if fresh.verified_at is not None:
            return fresh
        raise ConflictProblem(
            "This claim changed while it was being verified. Check the record and try again."
        )

    row.verified_at = now
    row.last_checked_at = None
    row.last_check_ok = None
    row.failing_since = None
    row.consecutive_failures = 0

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="domain.verified",
        entity="org_domain",
        entity_id=row.id,
        after={"domain": row.domain, "verified": True},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DOMAIN_VERIFIED,
        aggregate_type="org_domain",
        aggregate_id=row.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "domain_id": str(row.id),
            "domain": row.domain,
        },
        actor=_actor(ctx),
    )
    return row


async def release_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    row: OrgDomain,
    *,
    request: Request | None = None,
) -> None:
    """Release a domain. Once gone, `GET /internal/v1/domains/{domain}` finds nothing at all —
    no different, from auth-service's side, than a domain nobody ever claimed."""
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="domain.released",
        entity="org_domain",
        entity_id=row.id,
        before={"domain": row.domain, "verified": row.verified_at is not None},
        request=request,
    )
    domain_id, domain_name = row.id, row.domain
    await session.delete(row)
    await session.flush()
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DOMAIN_RELEASED,
        aggregate_type="org_domain",
        aggregate_id=domain_id,
        payload={
            "organization_id": str(ctx.organization_id),
            "domain_id": str(domain_id),
            "domain": domain_name,
        },
        actor=_actor(ctx),
    )


async def recheck_domain(
    session: AsyncSession,
    row: OrgDomain,
    *,
    resolver: dns_check.Resolver | None = None,
    now: datetime | None = None,
) -> OrgDomain:
    """Ask DNS again for a domain that is already verified, and record what it found.

    Called only by `app/cron.py`'s sweep — there is no route for this, deliberately, the same
    reasoning `tools/reconcile_permissions.py` states for why a cross-tenant maintenance
    operation is a driven process rather than an endpoint. A row that is not (or no longer)
    verified is the caller's job to skip; this function does not check, so an accidental call
    against a pending row would happily record a "check" against a record nobody has proven
    means anything yet.

    **Does not touch `verified_at` on an ordinary miss.** A TXT record missing for one sweep
    tick is routine — a registrar hiccup, a zone edit mid-flight, a resolver that timed out —
    and revoking single sign-on for an entire organization on the first miss would fail far
    more often than it protects anything. So a miss is *recorded* first: `last_check_ok`
    becomes `False`, `consecutive_failures` increments, `failing_since` is set the moment it
    was **last known good** (only on the transition into failing, never bumped forward on
    every subsequent miss, so "failing for eight days" stays an honest number), and
    `EVT_DOMAIN_CHECK_FAILED` is published once, on that same transition — not once per tick,
    which would make the event useless as a signal to alert on. A recovery clears both
    `failing_since` and `consecutive_failures` and publishes `EVT_DOMAIN_CHECK_RECOVERED`,
    once, the same way.

    **Only a *sustained* run demotes.** Once `app.domain.should_demote` says so —
    `failing_since` older than `domain.DEMOTION_FAILING_FOR` *and*
    `consecutive_failures >= domain.DEMOTION_MIN_CONSECUTIVE_FAILURES` — this function clears
    `verified_at` itself, sets `demoted_at`, and publishes `EVT_DOMAIN_DEMOTED`. That is the
    one case where a periodic check does change what SSO will route: the internal lookup
    (`api/internal.py`) filters on `verified_at IS NOT NULL`, so a demoted domain stops being
    usable the instant this commits. Logged at `warning`, in addition to the audit row and the
    event, because this is exactly the moment an operator wants to notice
    (`infrastructure/edge/RUNBOOK.md`). Re-earning verification after a demotion is an
    ordinary re-claim and re-verify; nothing here does it automatically.
    """
    at = now or utcnow()
    host = domain_module.verification_txt_host(row.domain)
    expected = domain_module.verification_txt_value(row.verification_token)
    outcome = await dns_check.check_txt(host, expected, resolver=resolver)

    if outcome is dns_check.Outcome.RESOLVER_ERROR:
        # **Our own resolver failing to answer is not a fact about the customer's domain.**
        # Only `last_checked_at` moves — `last_check_ok` stays whatever it last genuinely was,
        # `consecutive_failures` and `failing_since` are untouched, and `should_demote` is never
        # even asked. An outage on this side of the connection must never be able to demote a
        # domain on its own; see `dns_check.py`'s own docstring on `_RESOLVER_ERROR` for the
        # round-3 finding this closes.
        row.last_checked_at = at
        log.warning(
            "account.domain.recheck_resolver_error",
            organization_id=str(row.organization_id),
            domain_id=str(row.id),
        )
        await session.flush()
        return row

    found = outcome is dns_check.Outcome.FOUND
    was_failing = row.failing_since is not None
    row.last_checked_at = at
    row.last_check_ok = found

    if found:
        row.consecutive_failures = 0
        if was_failing:
            row.failing_since = None
            record_audit(
                session,
                AuditLog,
                organization_id=row.organization_id,
                actor_id=None,
                acting_as=DOMAIN_RECHECK_ACTOR,
                action="domain.check_recovered",
                entity="org_domain",
                entity_id=row.id,
                after={"domain": row.domain, "last_check_ok": True},
            )
            publish_event(
                session,
                OutboxEvent,
                organization_id=row.organization_id,
                event_type=EVT_DOMAIN_CHECK_RECOVERED,
                aggregate_type="org_domain",
                aggregate_id=row.id,
                payload={
                    "organization_id": str(row.organization_id),
                    "domain_id": str(row.id),
                    "domain": row.domain,
                },
            )
        await session.flush()
        return row

    row.consecutive_failures += 1
    if not was_failing:
        row.failing_since = at
        record_audit(
            session,
            AuditLog,
            organization_id=row.organization_id,
            actor_id=None,
            acting_as=DOMAIN_RECHECK_ACTOR,
            action="domain.check_failed",
            entity="org_domain",
            entity_id=row.id,
            after={"domain": row.domain, "last_check_ok": False},
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=row.organization_id,
            event_type=EVT_DOMAIN_CHECK_FAILED,
            aggregate_type="org_domain",
            aggregate_id=row.id,
            payload={
                "organization_id": str(row.organization_id),
                "domain_id": str(row.id),
                "domain": row.domain,
            },
        )

    if domain_module.should_demote(
        failing_since=row.failing_since, consecutive_failures=row.consecutive_failures, now=at
    ):
        row.verified_at = None
        row.demoted_at = at
        log.warning(
            "account.domain.demoted",
            organization_id=str(row.organization_id),
            domain_id=str(row.id),
            failing_since=row.failing_since.isoformat() if row.failing_since else None,
            consecutive_failures=row.consecutive_failures,
        )
        record_audit(
            session,
            AuditLog,
            organization_id=row.organization_id,
            actor_id=None,
            acting_as=DOMAIN_RECHECK_ACTOR,
            action="domain.demoted",
            entity="org_domain",
            entity_id=row.id,
            before={"verified": True},
            after={
                "domain": row.domain,
                "verified": False,
                "consecutive_failures": row.consecutive_failures,
            },
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=row.organization_id,
            event_type=EVT_DOMAIN_DEMOTED,
            aggregate_type="org_domain",
            aggregate_id=row.id,
            payload={
                "organization_id": str(row.organization_id),
                "domain_id": str(row.id),
                "domain": row.domain,
                "consecutive_failures": row.consecutive_failures,
            },
        )

    await session.flush()
    return row


# --------------------------------------------------------------------- invitations


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def invite(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: InvitationIn,
    *,
    request: Request | None = None,
) -> tuple[Invitation, str]:
    """Create an invitation and return it with its raw token, once.

    Only the hash is stored, so a database leak hands out no working invitations. The raw
    value is returned here and never again.
    """
    existing = await selectors.get_user_by_email(session, payload.email)
    if existing is not None:
        member = await selectors.get_membership_for_user(session, ctx.organization_id, existing.id)
        if member is not None and member.status != "removed":
            raise ConflictProblem("That person is already a member of this organization")

    await _resolve_assignable_roles(session, ctx, payload.role_codes)

    raw = secrets.token_urlsafe(32)
    invitation = Invitation(
        organization_id=ctx.organization_id,
        email=payload.email,
        token_hash=_hash_token(raw),
        role_codes=list(payload.role_codes),
        product_codes=list(payload.product_codes),
        invited_by=ctx.principal.sub,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    session.add(invitation)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="invitation.created",
        entity="invitation",
        entity_id=invitation.id,
        after={"email": payload.email, "roles": list(payload.role_codes)},
        request=request,
    )
    return invitation, raw


async def resolve_invitation_organization(session: AsyncSession, raw_token: str) -> uuid.UUID:
    """Read one invitation on the bypass session and return the organization it belongs to.

    This is the *lookup* half of accept's bypass-then-narrow (ADR-0002, path 4): the invitee
    holds a token, not an account, so there is no tenant to bind before the invitation itself
    says which one it is. `session` is the directory session — BYPASSRLS, SELECT-only — and
    this touches exactly one row of one table. Every write that follows happens under a
    tenant-scoped transaction in `accept_invitation`.

    A hash that matches nothing raises the *same* opaque message a forged or expired token
    gets. Nothing here distinguishes unknown from anything else, and validation of
    expiry/revocation is deliberately left to `accept_invitation` so it lives in one place —
    an attacker must not be able to learn from this call that a token was ever real.
    """
    invitation = await selectors.get_invitation_by_hash(session, _hash_token(raw_token))
    if invitation is None:
        raise NotFoundProblem("That invitation is not valid")
    return invitation.organization_id


async def accept_invitation(
    session: AsyncSession, raw_token: str, full_name: str, *, request: Request | None = None
) -> Membership:
    invitation = await selectors.get_invitation_by_hash(session, _hash_token(raw_token))
    # One message for every failure mode: a distinct "expired" reply tells an attacker
    # their guess was a real token.
    invalid = NotFoundProblem("That invitation is not valid")
    if invitation is None or invitation.accepted_at or invitation.revoked_at:
        raise invalid
    if invitation.expires_at <= datetime.now(UTC):
        raise invalid

    membership = await add_member(
        session,
        org_id=invitation.organization_id,
        email=invitation.email,
        full_name=full_name,
        actor_user_id=invitation.invited_by,
        role_codes=list(invitation.role_codes),
        request=request,
    )
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_user_id = membership.user_id
    return membership


async def revoke_invitation(
    session: AsyncSession,
    ctx: ScopeContext,
    invitation: Invitation,
    *,
    request: Request | None = None,
) -> Invitation:
    if invitation.accepted_at is not None:
        raise BusinessRuleProblem("That invitation has already been accepted")
    invitation.revoked_at = datetime.now(UTC)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="invitation.revoked",
        entity="invitation",
        entity_id=invitation.id,
        after={"email": invitation.email},
        request=request,
    )
    return invitation


# --------------------------------------------------------------------- registration


@dataclass(slots=True)
class RegistrationDiff:
    """What `install_registration` did to reconcile a registration against one
    organization's roles (ADR-0007 §1).

    Roles created from scratch are unchanged first-write behaviour and are not "changed"
    in the sense this diff cares about — nobody's capabilities moved, because nobody held
    the role a moment ago. `grants_added` and `scopes_widened` are the two ways an
    *existing* system role's grant set can grow, and are what decide whether the `pv`
    bump fires (ADR-0007 §5) and what `memberships_without` is computed against (§4).
    """

    roles_created: list[str] = field(default_factory=list)
    #: `(role_code, permission_code, scope)` — a permission the role did not hold at all.
    grants_added: list[tuple[str, str, str]] = field(default_factory=list)
    #: `(role_code, permission_code, old_scope, new_scope)` — a permission the role held
    #: at a narrower scope than the template now names.
    scopes_widened: list[tuple[str, str, str, str]] = field(default_factory=list)
    #: Role codes the registration names that exist in this organization but are
    #: customer-authored (`is_system = False`) — left untouched, per ADR-0007 §4.
    skipped_customer_roles: list[str] = field(default_factory=list)
    #: `role_code -> sorted "code:scope"` grant list, before and after — only for roles
    #: that were actually widened, in the shape `role.grants_changed` already audits with.
    role_grants_before: dict[str, list[str]] = field(default_factory=dict)
    role_grants_after: dict[str, list[str]] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        """Whether anybody's *capabilities* moved.

        Creating a role nobody holds yet, or installing a permission row, changes what
        *exists* in the catalogue, not what anybody *may do* — so it must not drive the
        `pv` bump. This is the same rule `install_product_registration` already applied to
        the admin-role grant, extended to cover widening (ADR-0007 §5).
        """
        return bool(self.grants_added or self.scopes_widened)

    def permission_codes(self) -> list[str]:
        """Every permission code touched by a widen, for the `pv` bump's audience and the
        `memberships_without` count."""
        codes = {code for _, code, _ in self.grants_added}
        codes.update(code for _, code, _, _ in self.scopes_widened)
        return sorted(codes)

    def role_codes(self) -> list[str]:
        """Every role code that was actually widened."""
        codes = {code for code, _, _ in self.grants_added}
        codes.update(code for code, _, _, _ in self.scopes_widened)
        return sorted(codes)


async def install_registration(
    session: AsyncSession, org_id: uuid.UUID, registration: ProductRegistration
) -> RegistrationDiff:
    """Install a product's permissions and reconcile its role templates for an org.

    Idempotent, and a **reconcile** rather than a first-write (ADR-0007 §1): called on
    organization creation, on every product activation, and by
    `tools/reconcile_permissions.py` for a tenant that already exists. For each role
    template:

    - **missing** → created with its grants, exactly as before.
    - **present and system** (`is_system = True`) → **widened**: every `(permission,
      scope)` the template names and the role lacks is added, and an existing grant whose
      scope is narrower than the template's is raised. Nothing is ever removed or
      narrowed — union, not synchronisation, so this can never revoke.
    - **present and customer-authored** (`is_system = False`) → left alone entirely, and
      named in the returned diff so the caller can report the gap rather than close it
      (ADR-0007 §4) — automatically granting a capability to a role a customer built
      themselves is the "HR admin holds every permission" failure ADR-0000 §5's corollary
      exists to prevent.
    """
    for spec in registration.catalogue.specs:
        code = spec.code(registration.product)
        permission = await selectors.get_permission_by_code(session, code)
        if permission is None:
            permission = Permission(
                code=code,
                product=registration.product,
                module=spec.module,
                resource=spec.resource,
                action=spec.action,
                description=spec.description,
                default_scope=spec.default_scope,
                max_scope=spec.max_scope,
                is_dangerous=spec.is_dangerous,
            )
            session.add(permission)
    await session.flush()

    diff = RegistrationDiff()

    for template in registration.role_templates:
        role = await selectors.get_role_by_code(session, org_id, template.code)

        if role is None:
            role = Role(
                organization_id=org_id,
                product=template.product,
                code=template.code,
                name=template.name,
                description=template.description,
                is_default=template.is_default,
                is_system=True,
            )
            session.add(role)
            await session.flush()
            for grant in template.grants:
                permission = await selectors.get_permission_by_code(session, grant.code)
                if permission is None:
                    continue
                session.add(
                    RolePermission(role_id=role.id, permission_id=permission.id, scope=grant.scope)
                )
            diff.roles_created.append(template.code)
            continue

        if not role.is_system:
            diff.skipped_customer_roles.append(template.code)
            continue

        # Widen: a set union over (permission_code, scope). `role.grants` is loaded
        # (`lazy="selectin"`) as part of the query above, so this reads what is already
        # in memory rather than issuing another round trip.
        before_snapshot = sorted(f"{g.permission.code}:{g.scope}" for g in role.grants)
        existing = {g.permission.code: g for g in role.grants}
        role_changed = False
        for grant in template.grants:
            permission = await selectors.get_permission_by_code(session, grant.code)
            if permission is None:
                continue
            # Defence in depth: the template was already built against the permission's
            # ceiling (`PermissionCatalogue.all`/`.pick`), but a reconcile is exactly the
            # place a stale template could otherwise smuggle a wider grant through.
            target_scope = min_scope(grant.scope, permission.max_scope)
            current = existing.get(grant.code)
            if current is None:
                # `role=role, permission=permission` rather than the `*_id` columns used
                # above: the back-populated relationship appends to `role.grants` in
                # memory immediately, so the `after` snapshot below sees it without a
                # second query.
                session.add(RolePermission(role=role, permission=permission, scope=target_scope))
                diff.grants_added.append((template.code, grant.code, target_scope))
                role_changed = True
            elif _SCOPE_RANK[target_scope] > _SCOPE_RANK[current.scope]:
                old_scope = current.scope
                current.scope = target_scope
                diff.scopes_widened.append((template.code, grant.code, old_scope, target_scope))
                role_changed = True

        if role_changed:
            diff.role_grants_before[template.code] = before_snapshot
            diff.role_grants_after[template.code] = sorted(
                f"{g.permission.code}:{g.scope}" for g in role.grants
            )

    return diff


@dataclass(slots=True)
class ReconcileSummary:
    """The per-organization report line ADR-0007 §4 names:
    `{org_id, roles_created, grants_added, scopes_widened, memberships_without}`."""

    organization_id: uuid.UUID
    roles_created: list[str]
    grants_added: int
    scopes_widened: int
    skipped_customer_roles: list[str]
    memberships_without: int
    changed: bool


async def reconcile_registration(
    session: AsyncSession,
    org_id: uuid.UUID,
    registration: ProductRegistration,
    *,
    acting_as: str,
    dry_run: bool = False,
) -> ReconcileSummary:
    """Reconcile one organization against `registration` — what
    `tools/reconcile_permissions.py` drives, one call per organization, inside that
    organization's own transaction (ADR-0007 §3).

    An empty diff writes nothing: no audit row ("nothing happened" is not an event —
    ADR-0007 §6), no outbox event, and no `pv` bump. A non-empty diff writes exactly one
    audit row and publishes exactly one `core.grants.changed` event for the whole
    organization — never per membership, because a widening touches every membership in
    the tenant and that fan-out has no consumer today (ADR-0007 §5) — and only then bumps
    `pv`, inside this same transaction: if the transaction rolls back, the version has
    still moved, which costs one round of token refreshes and grants nobody anything, the
    safe direction to be wrong in.

    `dry_run` runs the identical reconcile — the same diff, computed the same way — but
    skips the audit row, the event and the bump. The caller (the CLI) is the one that
    decides not to commit; this function's only job is to not pretend anything happened
    when nothing was meant to.
    """
    diff = await install_registration(session, org_id, registration)

    memberships_without = 0
    if diff.changed:
        memberships_without = await selectors.count_memberships_missing_grants(
            session, org_id, diff.permission_codes()
        )
        if not dry_run:
            record_audit(
                session,
                AuditLog,
                organization_id=org_id,
                actor_id=None,
                acting_as=acting_as,
                action="permissions.reconciled",
                entity="organization",
                entity_id=org_id,
                before={"roles": diff.role_grants_before},
                after={"roles": diff.role_grants_after},
            )
            publish_event(
                session,
                OutboxEvent,
                organization_id=org_id,
                event_type=EVT_GRANTS_CHANGED,
                aggregate_type="organization",
                aggregate_id=org_id,
                payload={
                    "organization_id": str(org_id),
                    "role_codes": diff.role_codes(),
                    "permission_codes": diff.permission_codes(),
                },
            )
            # Fail-loud, inside this organization's transaction: `bump_permission_version`
            # raises when Redis is unreachable, which rolls this org's writes back and
            # leaves it to be picked up by the next run — never reported as reconciled.
            await _invalidate_grants(org_id)

    return ReconcileSummary(
        organization_id=org_id,
        roles_created=diff.roles_created,
        grants_added=len(diff.grants_added),
        scopes_widened=len(diff.scopes_widened),
        skipped_customer_roles=diff.skipped_customer_roles,
        memberships_without=memberships_without,
        changed=diff.changed,
    )


async def install_product_registration(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    manifest: ProductManifest,
    acting_as: str,
    grant_admin_to_owners: bool = True,
) -> dict[str, Any]:
    """Install a product's manifest for one organization — the activation-time half of
    registration, driven by the `core.product.activated` consumer and by
    `tools/wire_product.py`.

    Builds on `install_registration` (idempotent, so re-activation re-installs safely) and
    adds the piece an activation needs that organization creation does not: the product's
    admin-most role template is granted to the organization's active **owners**. The owner is
    the only principal guaranteed to exist at activation time, activation is owner-gated
    (`core.product.activation.write` sits on `ORG_OWNER` alone), and a product whose admin
    role has no holder is dead until somebody phones support — the owner can hand the role to
    the person who will actually run the product, and drop their own, through the ordinary
    role APIs. Members who join later pick up the product's `is_default` template exactly as
    they pick up `MEMBER`.

    The permission-version bump happens only when a grant was actually added: installing
    permissions and instantiating roles changes what *exists*, not what anybody may do, so a
    pure re-install must not invalidate every token in the organization. As of ADR-0007,
    that now also covers a role that already existed being *widened* — a product whose
    catalogue grew since an organization first activated it picks up the new grants on the
    next activation-time install, not only through the separate fleet reconciler.
    """
    registration = manifest.registration
    diff = await install_registration(session, org_id, registration)

    granted: list[uuid.UUID] = []
    granted_roles: list[str] = []
    if grant_admin_to_owners and manifest.admin_role_codes:
        owners = await selectors.list_owner_memberships(session, org_id)
        # Every admin template, not just one: a product spans as many services as it needs,
        # and each ships the admin role for its own slice. Granting one left CRM's owners
        # able to read contacts and not deals.
        for code in manifest.admin_role_codes:
            role = await selectors.get_role_by_code(session, org_id, code)
            if role is None:
                continue
            granted_roles.append(code)
            for owner in owners:
                if any(link.role_id == role.id for link in owner.roles):
                    continue
                session.add(
                    MembershipRole(membership_id=owner.id, role_id=role.id, granted_by=None)
                )
                if owner.id not in granted:
                    granted.append(owner.id)
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=None,
        acting_as=acting_as,
        action="product.registration_installed",
        entity="organization",
        entity_id=org_id,
        after={
            "product": registration.product,
            "permissions": registration.catalogue.codes(),
            "role_templates": [t.code for t in registration.role_templates],
            "admin_roles": list(manifest.admin_role_codes),
            "admin_role_granted_to": [str(m) for m in granted],
            "widened_roles": diff.role_codes(),
        },
    )
    for membership_id in granted:
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_GRANTS_CHANGED,
            aggregate_type="membership",
            aggregate_id=membership_id,
            payload={
                "organization_id": str(org_id),
                "membership_id": str(membership_id),
                "granted_roles": granted_roles,
            },
        )
    if granted or diff.changed:
        # A grant landed — either an owner picked up the admin role, or an existing
        # system role was widened (ADR-0007 §1, §5) — so tokens minted before it are
        # stale, same rule as every other write that changes what somebody may do.
        await _invalidate_grants(org_id)

    return {
        "product": registration.product,
        "permissions": len(registration.catalogue.specs),
        "role_templates": [t.code for t in registration.role_templates],
        "admin_roles": granted_roles,
        "owners_granted": len(granted),
        "grants_added": len(diff.grants_added),
        "scopes_widened": len(diff.scopes_widened),
    }


# ------------------------------------------------------- employee links (ADR-0008 §8)


@dataclass(frozen=True, slots=True)
class EmployeeLink:
    """One row of employee-service's `GET /internal/v1/employee-links`.

    Two UUIDs and nothing else — no name, no employee number, no PII (ADR-0008 §8).
    `membership_id` is null for an employee nobody ever pointed at a membership; those rows
    still matter, because they are what makes an `employee_id` account holds *recognised*.
    """

    employee_id: uuid.UUID
    membership_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LinkConflict:
    """A disagreement the reconcile reports and deliberately does not resolve.

    `kind` is `"employee_taken"` (employee says E↔M, but a different membership already
    holds E) or `"disagree"` (employee says E↔M, but M already holds a different employee).
    """

    kind: str
    employee_id: uuid.UUID
    membership_id: uuid.UUID
    held_by_membership_id: uuid.UUID | None


@dataclass(slots=True)
class LinkReconcileSummary:
    """The per-organization report line ADR-0008 §8 names."""

    organization_id: uuid.UUID
    links_written: int
    conflicts: list[LinkConflict]
    unrecognised_employee_ids: list[uuid.UUID]
    unrecognised_membership_ids: list[uuid.UUID]
    memberships_unlinked: int
    changed: bool

    @property
    def conflicts_employee_taken(self) -> int:
        return sum(1 for c in self.conflicts if c.kind == "employee_taken")

    @property
    def conflicts_disagree(self) -> int:
        return sum(1 for c in self.conflicts if c.kind == "disagree")


async def reconcile_employee_links(
    session: AsyncSession,
    org_id: uuid.UUID,
    links: list[EmployeeLink],
    *,
    acting_as: str,
    dry_run: bool = False,
) -> LinkReconcileSummary:
    """Fill and verify one organization's employee links — what
    `tools/reconcile_membership_links.py` drives, one call per organization, inside that
    organization's own transaction (ADR-0008 §8).

    `links` is employee-service's full answer for this organization: **every** employee with
    its `membership_id`, null or not. One page set serves both halves of the job.

    **Fill.** For every row whose `membership_id` is not null, set that membership's
    `employee_id`. Three rules, none of them negotiable:

    * **It only ever fills a null.** A membership account has already linked is never
      overwritten — not to a different value, not to the same one. That is what makes this
      re-runnable, safe to run beside live traffic, safe to interrupt, and what removes any
      need for a version gate (ADR-0008 §6): the interactive path is always the winner.
    * **Conflicts are reported, never resolved.** An employee already held by a different
      membership, or a membership already holding a different employee, is counted and
      returned. An automatic fix to a disagreement about who somebody *is* is not a fix.
    * **`pv` is never bumped.** Every write here is grant-shaped — a null becoming a value —
      and ADR-0008 §7 says grant-shaped changes wait for the token. It also keeps a fleet run
      from being a fleet-wide refresh storm. There is deliberately no `_invalidate_grants`
      call anywhere in this function.

    **Verify.** Every `employee_id` account holds that employee-service does not recognise is
    returned in `unrecognised_employee_ids`. That is ADR-0008 §4's mistyped UUID — a
    principal whose OWN scope silently resolves to nothing — and this pass is the only
    mechanism in the platform that finds it.

    An empty diff writes no audit row: "nothing happened" is not an event. A non-empty one
    writes exactly one organization-level row in this same transaction, carrying the
    counters. No outbox event: nothing consumes one, and a contract with no consumer is a
    compatibility obligation bought for nothing (ADR-0008 §3).
    """
    memberships = await selectors.list_memberships(session, org_id)
    by_id = {m.id: m for m in memberships}
    # Who currently holds each employee id, maintained as rows are filled so two employee
    # rows naming the same employee inside one run conflict rather than racing the index.
    holder_by_employee: dict[uuid.UUID, uuid.UUID] = {
        m.employee_id: m.id for m in memberships if m.employee_id is not None
    }
    known_employee_ids = {link.employee_id for link in links}

    links_written = 0
    conflicts: list[LinkConflict] = []
    unrecognised_membership_ids: list[uuid.UUID] = []

    for link in links:
        if link.membership_id is None:
            continue
        membership = by_id.get(link.membership_id)
        if membership is None or membership.status == "removed":
            # A membership id account does not have, or one that has been revoked. Filling a
            # removed row would block the rehire's new membership behind ADR-0008 §5's
            # conflict for no gain, so this is reported rather than written.
            unrecognised_membership_ids.append(link.membership_id)
            continue
        if membership.employee_id == link.employee_id:
            continue  # already true; the verify half below still counts it as recognised
        if membership.employee_id is not None:
            conflicts.append(
                LinkConflict(
                    kind="disagree",
                    employee_id=link.employee_id,
                    membership_id=membership.id,
                    held_by_membership_id=None,
                )
            )
            continue
        holder = holder_by_employee.get(link.employee_id)
        if holder is not None:
            conflicts.append(
                LinkConflict(
                    kind="employee_taken",
                    employee_id=link.employee_id,
                    membership_id=membership.id,
                    held_by_membership_id=holder,
                )
            )
            continue
        membership.employee_id = link.employee_id
        holder_by_employee[link.employee_id] = membership.id
        links_written += 1

    unrecognised_employee_ids = sorted(
        (e for e in holder_by_employee if e not in known_employee_ids), key=str
    )

    # Flushed before the count so it sees this run's writes; the caller owns the commit, and
    # under `--dry-run` the caller rolls the whole transaction back regardless.
    await session.flush()
    memberships_unlinked = await selectors.count_memberships_unlinked(session, org_id)

    changed = links_written > 0
    if changed and not dry_run:
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=None,
            acting_as=acting_as,
            action="membership.links_reconciled",
            entity="organization",
            entity_id=org_id,
            after={
                "links_written": links_written,
                "conflicts_employee_taken": sum(1 for c in conflicts if c.kind == "employee_taken"),
                "conflicts_disagree": sum(1 for c in conflicts if c.kind == "disagree"),
                "unrecognised_employee_ids": [str(e) for e in unrecognised_employee_ids],
                "unrecognised_membership_ids": [str(m) for m in unrecognised_membership_ids],
                "memberships_unlinked": memberships_unlinked,
            },
        )

    return LinkReconcileSummary(
        organization_id=org_id,
        links_written=links_written,
        conflicts=conflicts,
        unrecognised_employee_ids=unrecognised_employee_ids,
        unrecognised_membership_ids=unrecognised_membership_ids,
        memberships_unlinked=memberships_unlinked,
        changed=changed,
    )
