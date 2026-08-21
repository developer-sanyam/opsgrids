"""The internal API — service-to-service, never reachable from outside the cluster.

account-service owns *who exists*. auth-service owns credentials, and the gateways own
request admission; both need to ask this service questions that no user token can carry:

- auth, at sign-in, needs the user and every organization they belong to — **before** an
  organization has been chosen, so before any tenant scope exists.
- the gateways, on every authenticated request, need the caller's grants, because tokens
  deliberately carry no permission list.

These three read across tenants, which is why they live in their own module behind
`require_service_token` rather than beside the user-facing routes where the tenant guard is
the default.

They are **not** the only cross-tenant paths in the platform, and an earlier version of this
docstring wrongly said they were. `POST /api/v1/invitations/accept` is a fourth: it is public
by design — the invitee holds a token, not yet an account — and unlike these three it
**writes**, creating a user, a membership and role grants with no organization in context.
`selectors.get_user_by_email` and `get_invitation_by_hash` are likewise unfiltered on purpose.
Anyone reasoning about tenancy from this file needs all four, so the claim is corrected rather
than deleted. See docs/adr/0002-row-level-security.md, which depends on the full list.

Mounted at the root, not under `/api/v1`: these paths are not public API and must not
appear in the customer-facing OpenAPI document.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Membership, MembershipRole, Organization, Role, User
from platform_core.auth import require_service_token
from platform_core.cache import current_permission_version
from platform_core.db import get_session
from platform_core.errors import NotFoundProblem

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Not `include_in_schema=False` on each route: the whole router is excluded, so a
    # route added here later cannot leak into the public document by omission.
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _serialise(session: AsyncSession, user: User) -> dict[str, Any]:
    """Shape a user the way `auth.directory.DirectoryUser` parses it.

    Deleted and non-active memberships are included with their real status rather than
    filtered out. Authentication decides what counts as usable; the directory's job is to
    report, and a caller that cannot see a suspended membership cannot explain to the
    person why they are locked out.
    """
    memberships = (
        await session.execute(
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(Membership.user_id == user.id)
            .options(selectinload(Membership.roles).selectinload(MembershipRole.role))
        )
    ).all()

    out = []
    for membership, organization in memberships:
        # The version lives in Redis, not in a column: it is bumped on every grant change
        # and read on every request, which is a cache's job rather than a table's.
        version = await current_permission_version(str(organization.id))
        out.append(
            {
                "membership_id": str(membership.id),
                "organization_id": str(organization.id),
                "organization_slug": organization.slug,
                "organization_name": organization.name,
                # An organization that is suspended makes every membership in it
                # unusable, whatever the membership itself says.
                "status": (
                    membership.status if organization.status != "suspended" else "suspended"
                ),
                "roles": [link.role.code for link in membership.roles],
                "locations": (
                    [c.strip() for c in membership.location_codes.split(",") if c.strip()]
                    if membership.location_codes
                    else []
                ),
                "permission_version": version or 0,
                "employee_id": str(membership.employee_id) if membership.employee_id else None,
            }
        )

    return {
        "user_id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active and user.deleted_at is None,
        "memberships": out,
    }


@router.get("/users/by-email", summary="Look a person up by email, across all tenants")
async def user_by_email(session: SessionDep, email: Annotated[str, Query()]) -> dict[str, Any]:
    """Sign-in starts here, before any organization is known.

    Email is unique platform-wide among live users, which is what makes one person able to
    belong to several organizations without holding several accounts.
    """
    user = (
        await session.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        # 404 rather than an empty body: `HttpDirectory` maps it to `None`, and the caller
        # turns that into the same "wrong email or password" a bad password gets.
        raise NotFoundProblem("No such user")
    return await _serialise(session, user)


@router.get("/users/{user_id}", summary="One person, with every membership")
async def user_by_id(session: SessionDep, user_id: uuid.UUID) -> dict[str, Any]:
    user = (
        await session.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundProblem("No such user")
    return await _serialise(session, user)


@router.get(
    "/memberships/{membership_id}/grants",
    summary="Everything this membership may do, as `code:SCOPE`",
)
async def membership_grants(session: SessionDep, membership_id: uuid.UUID) -> dict[str, Any]:
    """What the gateway asks on every authenticated request.

    A membership that is not active returns **no grants rather than 404**. The distinction
    matters: 404 means "no such membership" and the gateway treats it as an empty grant
    set anyway, but returning an explicit empty list for a suspended person is the honest
    answer and keeps the two cases separable in logs.

    Where the same permission arrives through two roles at different scopes, the widest
    wins — a role that grants ORG is not narrowed by another that grants OWN.
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

    if membership is None:
        raise NotFoundProblem("No such membership")
    if membership.status != "active":
        return {"grants": [], "permission_version": 0}

    widest: dict[str, str] = {}
    for link in membership.roles:
        for grant in link.role.grants:
            code = grant.permission.code
            if code not in widest or _rank(grant.scope) > _rank(widest[code]):
                widest[code] = grant.scope

    version = await current_permission_version(str(membership.organization_id))
    return {
        "grants": sorted(f"{code}:{scope}" for code, scope in widest.items()),
        "permission_version": version or 0,
    }


def _rank(scope: str) -> int:
    return {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}.get(scope, 0)
