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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.catalogue import CORE_REGISTRATION, ProductRegistration
from app.models import (
    AuditLog,
    Invitation,
    Location,
    Membership,
    MembershipRole,
    Organization,
    OrgUnit,
    OutboxEvent,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.schemas import (
    GrantIn,
    InvitationIn,
    LocationIn,
    MemberPatch,
    OrganizationIn,
    OrganizationPatch,
    OrgUnitIn,
    RoleIn,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.cache import bump_permission_version
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `core.*` because these are the platform's own, not a product's.
EVT_ORG_CREATED = "core.org.created"
EVT_ORG_UPDATED = "core.org.updated"
EVT_UNIT_CREATED = "core.org_unit.created"
EVT_UNIT_UPDATED = "core.org_unit.updated"
EVT_LOCATION_CREATED = "core.location.created"
EVT_USER_CREATED = "core.user.created"
EVT_MEMBERSHIP_CREATED = "core.membership.created"
EVT_MEMBERSHIP_UPDATED = "core.membership.updated"
EVT_MEMBERSHIP_REVOKED = "core.membership.revoked"
EVT_GRANTS_CHANGED = "core.grants.changed"


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
) -> Organization:
    """Create a customer.

    Not scoped by an organization — this is the one write that brings one into existence,
    so it is guarded by platform staff rather than by `require(...)`.
    """
    if await selectors.slug_taken(session, payload.slug):
        raise ConflictProblem(f"The address {payload.slug!r} is already taken")

    org = Organization(
        **payload.model_dump(exclude_none=True),
        status="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=14),
        created_by=str(actor_user_id) if actor_user_id else "system",
    )
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
    for field, value in changes.items():
        setattr(org, field, value)
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


async def update_member(
    session: AsyncSession,
    ctx: ScopeContext,
    membership: Membership,
    payload: MemberPatch,
    *,
    request: Request | None = None,
) -> Membership:
    changes = payload.model_dump(exclude_unset=True)

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
    }

    if "location_codes" in changes and changes["location_codes"] is not None:
        membership.location_codes = ",".join(changes.pop("location_codes"))
    changes.pop("location_codes", None)
    for field, value in changes.items():
        setattr(membership, field, value)

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
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    # Locations, department and reporting line all feed scope resolution, and `loc` is
    # carried in the token — so a move between sites has to invalidate it too. A change
    # to job title or display name does not.
    if (
        before["location_codes"] != membership.location_codes
        or before["org_unit_id"] != str(membership.org_unit_id or "")
        or before["manager_membership_id"] != str(membership.manager_membership_id or "")
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

    resolved: list[Role] = []
    for code in role_codes:
        role = await selectors.get_role_by_code(session, ctx.organization_id, code)
        if role is None:
            raise NotFoundProblem(f"No role named {code!r} in this organization")
        resolved.append(role)

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
    await _replace_grants(session, role, payload.grants)
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
    grants: list[GrantIn],
    *,
    request: Request | None = None,
) -> Role:
    if role.is_system:
        raise BusinessRuleProblem(
            "A system role cannot be edited. Clone it and change the copy, so the "
            "platform's defaults stay recognisable."
        )
    before = [f"{g.permission.code}:{g.scope}" for g in role.grants]
    await _replace_grants(session, role, grants)
    await session.refresh(role, ["grants"])

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="role.grants_changed",
        entity="role",
        entity_id=role.id,
        before={"grants": sorted(before)},
        after={"grants": sorted(f"{g.permission_code}:{g.scope}" for g in grants)},
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


async def _replace_grants(session: AsyncSession, role: Role, grants: list[GrantIn]) -> None:
    """Set a role's grants, refusing anything wider than the permission allows.

    `max_scope` is the reason a role editor cannot quietly turn a self-service permission
    into an organization-wide one.
    """
    rank = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}
    # An explicit DELETE rather than mutating the collection: touching `role.grants` on a
    # freshly flushed object triggers a lazy load, which raises MissingGreenlet under
    # asyncio. It is also one statement instead of N.
    await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))

    for grant in grants:
        permission = await selectors.get_permission_by_code(session, grant.permission_code)
        if permission is None:
            raise NotFoundProblem(f"No permission named {grant.permission_code!r}")
        if rank[grant.scope] > rank[permission.max_scope]:
            raise BusinessRuleProblem(
                f"{permission.code} cannot be granted at {grant.scope}; "
                f"the widest it supports is {permission.max_scope}"
            )
        session.add(RolePermission(role_id=role.id, permission_id=permission.id, scope=grant.scope))


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

    for code in payload.role_codes:
        if await selectors.get_role_by_code(session, ctx.organization_id, code) is None:
            raise NotFoundProblem(f"No role named {code!r} in this organization")

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


async def install_registration(
    session: AsyncSession, org_id: uuid.UUID, registration: ProductRegistration
) -> None:
    """Install a product's permissions and instantiate its role templates for an org.

    Idempotent: called on organization creation and again whenever a product is activated,
    so it upserts rather than assuming a clean slate.
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

    for template in registration.role_templates:
        if await selectors.get_role_by_code(session, org_id, template.code):
            continue
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
