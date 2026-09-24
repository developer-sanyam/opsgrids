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
from sqlalchemy.orm import contains_eager, selectinload

from app import domain as domain_rules
from app.models import (
    Invitation,
    Location,
    Membership,
    MembershipRole,
    Organization,
    OrgDomain,
    OrgUnit,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.schemas import DomainOut, GrantOut, MemberOut, OrgUnitNode, RoleOut
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
    linked: bool | None = None,
    employee_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered member list. `ctx.apply` is the enforcement point."""
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        # `contains_eager` attaches the joined `User` to `Membership.user`. Without it the
        # join populates the second column but leaves the relationship *unloaded*, so a
        # caller that pages to the memberships and then reads `m.user` triggers a lazy load
        # — sync IO inside an async request, which raises MissingGreenlet rather than
        # returning a page. Joining and eager-loading are not the same thing.
        .options(contains_eager(Membership.user))
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
    if linked is not None:
        # ADR-0008 §9: `?linked=false` is the screen an administrator uses to find the people
        # whose approval inbox is silently empty. It is a filter **within** the scope applied
        # above — ANDed onto it, never in place of it — so it narrows what the caller can
        # already see and can never widen it. A TEAM-scoped caller asking for unlinked members
        # gets unlinked members *of their team*.
        stmt = stmt.where(
            Membership.employee_id.is_not(None) if linked else Membership.employee_id.is_(None)
        )
    if employee_id is not None:
        # "Which member is this employee?" — what the HRMS employee page asks to show its
        # "Linked member" field (ADR-0008 Q1). Like `linked`, ANDed onto the scope above and
        # never in place of it, so it can only narrow: a TEAM-scoped caller naming an employee
        # who is linked to someone outside their team gets an empty page, not that member. The
        # partial unique index makes the answer at most one row.
        stmt = stmt.where(Membership.employee_id == employee_id)

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


async def list_owner_memberships(session: AsyncSession, org_id: uuid.UUID) -> list[Membership]:
    """The organization's active owners.

    Who a newly installed product's admin role is handed to: the owner is the only principal
    guaranteed to exist when a product is activated, and activation is owner-gated
    (`core.product.activation.write` is granted to `ORG_OWNER` alone).
    """
    stmt = select(Membership).where(
        Membership.organization_id == org_id,
        Membership.is_owner.is_(True),
        Membership.status == "active",
    )
    return list((await session.execute(stmt)).scalars())


async def count_memberships_missing_grants(
    session: AsyncSession, org_id: uuid.UUID, permission_codes: list[str]
) -> int:
    """Active memberships holding **none** of `permission_codes`, through any role.

    What ADR-0007 §4 calls `memberships_without`: a reconcile widens system roles only, so
    a membership whose sole role is customer-authored (or holds no role at all) gains
    nothing from it. This is the number an operator can act on, computed in one query
    rather than a loop per membership — a reconcile can touch every membership in the
    tenant, and a per-membership round trip here would be exactly the N+1 CLAUDE.md rules
    out.
    """
    if not permission_codes:
        return 0
    holds_one = (
        select(MembershipRole.membership_id)
        .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.code.in_(permission_codes))
    )
    stmt = (
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.organization_id == org_id,
            Membership.status == "active",
            Membership.id.notin_(holds_one),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def get_membership_by_employee(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> Membership | None:
    """The membership already holding `employee_id` in this organization, if any.

    The read behind ADR-0008 §5's pre-check. Deliberately **not** filtered to active
    memberships: `uq_memberships_org_employee` does not exclude removed rows either, and
    `revoke_member` keeps the link so an ordinary rehire reuses the same row — so a
    pre-check that skipped removed memberships would miss exactly the conflict the index is
    about to raise, and downgrade a legible 409 naming the holder into a raced one.

    At most one row can match: that is what the partial unique index guarantees, which is
    why this is `scalar_one_or_none` rather than a list.
    """
    stmt = select(Membership).where(
        Membership.organization_id == org_id,
        Membership.employee_id == employee_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_memberships(session: AsyncSession, org_id: uuid.UUID) -> list[Membership]:
    """Every membership of one organization, whatever its status, in one query.

    For `tools/reconcile_membership_links.py`, which needs three things from the same rows —
    the fill target for each `membership_id` employee-service names, the set of employee ids
    account already holds (for ADR-0008 §8's verify half), and who holds each one (for the
    conflict counters). A per-row lookup would be an N+1 across a whole tenant; removed
    memberships are included because they can hold a link and therefore cause a conflict.
    """
    stmt = select(Membership).where(Membership.organization_id == org_id).order_by(Membership.id)
    return list((await session.execute(stmt)).scalars().all())


async def count_memberships_unlinked(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Active memberships with no employee link — ADR-0008 §8's `memberships_unlinked`.

    Deliberately the same shape as ADR-0007 §4's `memberships_without`: the number an
    operator can act on, computed in one query rather than a loop, and reported rather than
    fixed, because an automatic answer to "who is this person" is not a fix.
    """
    stmt = (
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.organization_id == org_id,
            Membership.status == "active",
            Membership.employee_id.is_(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


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


async def org_unit_has_active_children(
    session: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID
) -> bool:
    """Whether another, still-active department reports to this one.

    Deactivating a department that still has live sub-departments would leave them pointing
    at a parent nobody administers any more — `move_org_unit` refuses a cycle for the same
    reason: an org chart has to stay something a person can act on.
    """
    stmt = (
        select(OrgUnit.id)
        .where(
            OrgUnit.organization_id == org_id,
            OrgUnit.parent_id == unit_id,
            OrgUnit.deleted_at.is_(None),
            OrgUnit.is_active.is_(True),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def org_unit_has_active_members(
    session: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID
) -> bool:
    """Whether anybody still active is assigned to this department."""
    stmt = (
        select(Membership.id)
        .where(
            Membership.organization_id == org_id,
            Membership.org_unit_id == unit_id,
            Membership.status == "active",
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


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


# --------------------------------------------------------------------- domains


async def list_domains(session: AsyncSession, org_id: uuid.UUID) -> list[OrgDomain]:
    stmt = (
        select(OrgDomain).where(OrgDomain.organization_id == org_id).order_by(OrgDomain.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_domain(
    session: AsyncSession, org_id: uuid.UUID, domain_id: uuid.UUID
) -> OrgDomain | None:
    stmt = select(OrgDomain).where(OrgDomain.id == domain_id, OrgDomain.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_domain_by_name(
    session: AsyncSession, org_id: uuid.UUID, domain: str
) -> OrgDomain | None:
    """This organization's own row for `domain`, if it has already claimed it.

    Tenant-scoped like every other selector here, and filtered by `organization_id` in the
    `WHERE` clause besides — `(organization_id, domain)` is unique
    (`uq_org_domains_org_domain`), not `domain` alone, so this answers "have I already claimed
    this", never "who else has": another organization's row for the same domain is a real,
    separate row this query does not reach.
    """
    stmt = select(OrgDomain).where(OrgDomain.organization_id == org_id, OrgDomain.domain == domain)
    return (await session.execute(stmt)).scalar_one_or_none()


def to_domain_out(row: OrgDomain) -> DomainOut:
    still_pending = row.verified_at is None
    return DomainOut(
        id=row.id,
        domain=row.domain,
        is_primary=row.is_primary,
        auto_join=row.auto_join,
        verified_at=row.verified_at,
        created_at=row.created_at,
        expires_at=domain_rules.claim_expires_at(row.created_at) if still_pending else None,
        verification_host=domain_rules.verification_txt_host(row.domain),
        verification_value=domain_rules.verification_txt_value(row.verification_token),
        last_checked_at=row.last_checked_at,
        last_check_ok=row.last_check_ok,
        failing_since=row.failing_since,
    )


async def due_for_recheck(session: AsyncSession, *, before: Any, limit: int) -> list[OrgDomain]:
    """Verified domains a periodic sweep should re-check: `last_checked_at` older than
    `before` (or never checked at all), oldest first.

    Runs on the BYPASSRLS directory reader — cross-tenant discovery of *which* domains are
    due, the same shape `app/cron.py`'s module docstring explains for the sweep as a whole —
    and costs no new grant: `opsgrids_directory` already reads `org_domains` for the internal
    SSO lookup (`api/internal.py`). Every write that follows runs per-domain on the ordinary,
    tenant-bound application role.
    """
    stmt = (
        select(OrgDomain)
        .where(
            OrgDomain.verified_at.is_not(None),
            or_(OrgDomain.last_checked_at.is_(None), OrgDomain.last_checked_at < before),
        )
        .order_by(OrgDomain.last_checked_at.asc().nulls_first())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------- roles


async def list_roles(session: AsyncSession, org_id: uuid.UUID) -> list[Role]:
    """An organization's own roles.

    No NULL branch any more. Templates live in `catalogue.py` as code and are instantiated
    as org-owned rows, so a role belonging to no organization cannot exist — ADR-0002,
    decision 1. Leaving the `IS NULL` arm would have quietly widened this query the moment
    anything wrote such a row.
    """
    stmt = select(Role).where(Role.organization_id == org_id).order_by(Role.code)
    return list((await session.execute(stmt)).scalars().all())


async def get_role(session: AsyncSession, org_id: uuid.UUID, role_id: uuid.UUID) -> Role | None:
    stmt = select(Role).where(Role.id == role_id, Role.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_role_by_code(session: AsyncSession, org_id: uuid.UUID, code: str) -> Role | None:
    stmt = select(Role).where(Role.organization_id == org_id, Role.code == code)
    return (await session.execute(stmt)).scalar_one_or_none()


async def role_held_by_a_membership(
    session: AsyncSession, org_id: uuid.UUID, role_id: uuid.UUID
) -> bool:
    """Whether any membership — active or not — still holds this role.

    Explicit, rather than trusting `ON DELETE CASCADE` on `membership_roles.role_id`: the
    cascade would strip the row silently, which is exactly how a colleague loses access with
    no audit line explaining why (the audit's "do NOT rely on ON DELETE CASCADE").
    """
    stmt = (
        select(MembershipRole.id)
        .join(Membership, Membership.id == MembershipRole.membership_id)
        .where(MembershipRole.role_id == role_id, Membership.organization_id == org_id)
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def role_named_by_a_pending_invitation(
    session: AsyncSession, org_id: uuid.UUID, role_code: str
) -> bool:
    """Whether an outstanding invitation still promises this role on acceptance."""
    stmt = (
        select(Invitation.id)
        .where(
            Invitation.organization_id == org_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.role_codes.contains([role_code]),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


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


_GRANT_SCOPE_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


async def get_membership_grants(
    session: AsyncSession, membership_id: uuid.UUID
) -> tuple[Membership | None, list[str]]:
    """The one query behind both `GET /internal/v1/memberships/{id}/grants` (auth's directory
    read) and `GET /api/v1/members/me/grants` (a member reading their own).

    Two callers computing "everything this membership may do" independently is how they drift
    — one forgets a role a person was just given, the other still shows a permission that was
    revoked five minutes ago, and nobody can say which is right because neither is wrong on its
    own terms. This is the single place "widest scope wins" is decided.

    Returns `(None, [])` for a membership that does not exist and `(membership, [])` for one
    that is not active — never a 404 for the second case: a caller reading their own grants
    while suspended gets the honest empty list its status implies, not an error.
    """
    membership = (
        await session.execute(
            select(Membership)
            .where(Membership.id == membership_id)
            .options(
                selectinload(Membership.roles)
                .selectinload(MembershipRole.role)
                .selectinload(Role.grants)
            )
        )
    ).scalar_one_or_none()

    if membership is None or membership.status != "active":
        return membership, []

    widest: dict[str, str] = {}
    for link in membership.roles:
        for grant in link.role.grants:
            code = grant.permission.code
            rank = _GRANT_SCOPE_RANK[grant.scope]
            if code not in widest or rank > _GRANT_SCOPE_RANK[widest[code]]:
                widest[code] = grant.scope

    return membership, sorted(f"{code}:{scope}" for code, scope in widest.items())


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
