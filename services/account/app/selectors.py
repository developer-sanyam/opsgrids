"""Reads for the account service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)`. A list endpoint that builds its own query
instead of coming through a selector is how an organization leaks, so routers never write
SQL.

The scope columns for a membership are:

    OWN      -> Membership.id                    (your own membership row)
    TEAM     -> Membership.manager_membership_id (people who report to you)
    LOCATION -> Membership.location_codes        (a CSV column, handled here)

LOCATION is resolved in this module rather than through the shared `apply` helper, because
the member's locations are a comma-separated string — it travels in a JWT claim as CSV —
and the shared helper deliberately refuses to guess at that shape.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Invitation,
    Location,
    Membership,
    MembershipRole,
    Organization,
    OrgUnit,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.schemas import GrantOut, MemberOut, OrgUnitNode, RoleOut
from platform_core.auth import Scope, ScopeContext

# --------------------------------------------------------------------- organizations


async def get_organization(session: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    stmt = select(Organization).where(Organization.id == org_id, Organization.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_organization_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    stmt = select(Organization).where(Organization.slug == slug, Organization.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def slug_taken(session: AsyncSession, slug: str) -> bool:
    return (await get_organization_by_slug(session, slug)) is not None


# --------------------------------------------------------------------- members


def split_codes(csv: str | None) -> list[str]:
    return [c for c in (csv or "").split(",") if c]


def _location_predicate(codes: list[str]) -> Any:
    """Match a member whose CSV location list contains any of `codes`.

    Padding both sides with commas is what stops `MUM` matching `MUMBAI-2`.
    """
    if not codes:
        return None
    padded = func.concat(",", func.coalesce(Membership.location_codes, ""), ",")
    return or_(*[padded.like(f"%,{code},%") for code in codes])


def members_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    org_unit_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered member list. `ctx.apply` is the enforcement point."""
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == ctx.organization_id,
            Membership.status != "removed",
            User.deleted_at.is_(None),
        )
    )

    if ctx.scope is Scope.LOCATION:
        predicate = _location_predicate(ctx.principal.locations)
        # No locations on the token means no rows — never every row.
        stmt = stmt.where(predicate) if predicate is not None else stmt.where(false())
    else:
        stmt = ctx.apply(
            stmt,
            owner_col=Membership.id,
            team_col=Membership.manager_membership_id,
        )

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.full_name).like(needle),
                func.lower(User.email).like(needle),
                func.lower(func.coalesce(Membership.job_title, "")).like(needle),
            )
        )
    if org_unit_id:
        stmt = stmt.where(Membership.org_unit_id == org_unit_id)
    if status:
        stmt = stmt.where(Membership.status == status)

    return stmt.order_by(User.full_name)


async def get_member(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> tuple[Membership, User] | None:
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.id == membership_id,
            Membership.organization_id == org_id,
            Membership.status != "removed",
        )
    )
    row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row else None


async def get_membership_for_user(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    stmt = select(Membership).where(
        Membership.organization_id == org_id, Membership.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Email is unique platform-wide, so this deliberately takes no organization."""
    stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


def to_member_out(membership: Membership, user: User) -> MemberOut:
    return MemberOut(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        display_name=membership.display_name,
        job_title=membership.job_title,
        status=membership.status,
        is_owner=membership.is_owner,
        org_unit_id=membership.org_unit_id,
        manager_membership_id=membership.manager_membership_id,
        location_codes=split_codes(membership.location_codes),
        employee_id=membership.employee_id,
        roles=sorted(mr.role.code for mr in membership.roles),
        joined_at=membership.joined_at,
        started_on=membership.started_on,
    )


# --------------------------------------------------------------------- structure


async def list_org_units(session: AsyncSession, org_id: uuid.UUID) -> list[OrgUnit]:
    stmt = (
        select(OrgUnit)
        .where(OrgUnit.organization_id == org_id, OrgUnit.deleted_at.is_(None))
        .order_by(OrgUnit.name)
    )
    return list((await session.execute(stmt)).scalars().all())


def build_tree(units: list[OrgUnit]) -> list[OrgUnitNode]:
    """Assemble the flat list into a forest, in one pass rather than N queries.

    A unit whose parent was soft-deleted surfaces at the root instead of vanishing —
    disappearing from the tree is how a department quietly loses its people.
    """
    nodes = {u.id: OrgUnitNode.model_validate(u) for u in units}
    roots: list[OrgUnitNode] = []
    for unit in units:
        node = nodes[unit.id]
        parent = nodes.get(unit.parent_id) if unit.parent_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            roots.append(node)
    return roots


async def get_org_unit(
    session: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID
) -> OrgUnit | None:
    stmt = select(OrgUnit).where(
        OrgUnit.id == unit_id,
        OrgUnit.organization_id == org_id,
        OrgUnit.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def descendant_ids(
    session: AsyncSession, org_id: uuid.UUID, root: uuid.UUID
) -> set[uuid.UUID]:
    """Every unit at or below `root`, cycle-safe.

    Walked in Python over the loaded tree rather than as a recursive CTE: an org chart is
    hundreds of rows, and the `seen` set means a cycle terminates instead of recursing.
    """
    units = await list_org_units(session, org_id)
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for u in units:
        if u.parent_id:
            children.setdefault(u.parent_id, []).append(u.id)

    seen: set[uuid.UUID] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, []))
    return seen


async def list_locations(session: AsyncSession, org_id: uuid.UUID) -> list[Location]:
    stmt = (
        select(Location)
        .where(Location.organization_id == org_id, Location.deleted_at.is_(None))
        .order_by(Location.name)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_location(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> Location | None:
    stmt = select(Location).where(
        Location.id == location_id,
        Location.organization_id == org_id,
        Location.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- roles


async def list_roles(session: AsyncSession, org_id: uuid.UUID) -> list[Role]:
    """An organization's own roles, plus the platform templates it may clone."""
    stmt = (
        select(Role)
        .where(or_(Role.organization_id == org_id, Role.organization_id.is_(None)))
        .order_by(Role.code)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_role(session: AsyncSession, org_id: uuid.UUID, role_id: uuid.UUID) -> Role | None:
    stmt = select(Role).where(Role.id == role_id, Role.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_role_by_code(session: AsyncSession, org_id: uuid.UUID, code: str) -> Role | None:
    stmt = select(Role).where(Role.organization_id == org_id, Role.code == code)
    return (await session.execute(stmt)).scalar_one_or_none()


def to_role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        product=role.product,
        is_default=role.is_default,
        is_system=role.is_system,
        grants=[
            # `scope` is a plain column; the Literal is enforced when a grant is written.
            GrantOut(permission_code=g.permission.code, scope=cast(Any, g.scope))
            for g in role.grants
        ],
    )


async def list_permissions(session: AsyncSession, product: str | None = None) -> list[Permission]:
    stmt = select(Permission).where(Permission.retired_at.is_(None))
    if product:
        stmt = stmt.where(Permission.product == product)
    return list((await session.execute(stmt.order_by(Permission.code))).scalars().all())


async def get_permission_by_code(session: AsyncSession, code: str) -> Permission | None:
    stmt = select(Permission).where(Permission.code == code, Permission.retired_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve_grants(session: AsyncSession, membership_id: uuid.UUID) -> list[str]:
    """Flatten a membership's roles into the `code:SCOPE` list a token issuer needs.

    Where the same permission arrives through two roles, **the widest scope wins** — the
    same rule `Principal.scope_for` applies at the far end, so the resolved list and the
    runtime check can never disagree.
    """
    stmt = (
        select(Permission.code, RolePermission.scope)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(MembershipRole, MembershipRole.role_id == Role.id)
        .where(MembershipRole.membership_id == membership_id, Permission.retired_at.is_(None))
    )
    rank = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}
    widest: dict[str, str] = {}
    for code, scope in (await session.execute(stmt)).all():
        if code not in widest or rank[scope] > rank[widest[code]]:
            widest[code] = scope
    return sorted(f"{code}:{scope}" for code, scope in widest.items())


# --------------------------------------------------------------------- invitations


async def list_invitations(
    session: AsyncSession, org_id: uuid.UUID, *, pending_only: bool = True
) -> list[Invitation]:
    stmt = select(Invitation).where(Invitation.organization_id == org_id)
    if pending_only:
        stmt = stmt.where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
    return list(
        (await session.execute(stmt.order_by(Invitation.created_at.desc()))).scalars().all()
    )


async def get_invitation_by_hash(session: AsyncSession, token_hash: str) -> Invitation | None:
    """Looked up by hash, never by raw token — the raw value is never stored."""
    stmt = select(Invitation).where(Invitation.token_hash == token_hash)
    return (await session.execute(stmt)).scalar_one_or_none()
