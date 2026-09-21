"""The internal API — service-to-service, never reachable from outside the cluster.

account-service owns *who exists*. auth-service owns credentials, and the gateways own
request admission; both need to ask this service questions that no user token can carry:

- auth, at sign-in, needs the user and every organization they belong to — **before** an
  organization has been chosen, so before any tenant scope exists.
- auth, when somebody starts single sign-on, needs the organization behind an email domain,
  which is the same shape of question: it is asked *in order to* discover which tenant this
  is, so there is no tenant to bind while asking.
- the gateways, on every authenticated request, need the caller's grants, because tokens
  deliberately carry no permission list.

These four read across tenants, which is why they live in their own module behind
`require_service_token` rather than beside the user-facing routes where the tenant guard is
the default.

They are **not** the only cross-tenant paths in the platform, and an earlier version of this
docstring wrongly said they were. `POST /api/v1/invitations/accept` is another: it is public
by design — the invitee holds a token, not yet an account — and unlike these four it
**writes**, creating a user, a membership and role grants. It does not run entirely without a
tenant, though: it uses *bypass-then-narrow* (ADR-0002, path 4) — the invitation is read on the
bypass session only to learn its organization, then every write happens under a tenant-scoped
transaction on the application role. The bypass touches one row of one table; the writes stay
enforced. `selectors.get_user_by_email` and `get_invitation_by_hash` are unfiltered on purpose.
Anyone reasoning about tenancy from this file needs all four, so the claim is corrected rather
than deleted. See docs/adr/0002-row-level-security.md, which depends on the full list.

Mounted at the root, not under `/api/v1`: these paths are not public API and must not
appear in the customer-facing OpenAPI document.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import selectors
from app.models import Membership, MembershipRole, Organization, OrgDomain, User
from platform_core.auth import require_service_token
from platform_core.cache import current_permission_version
from platform_core.db import get_directory_session
from platform_core.errors import NotFoundProblem
from platform_core.logging import get_logger

log = get_logger("account.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Not `include_in_schema=False` on each route: the whole router is excluded, so a
    # route added here later cannot leak into the public document by omission.
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

#: The cross-tenant reader: BYPASSRLS, SELECT-only, and used by nothing else.
#: These lookups resolve a person *before* an organization is known, so there is no
#: tenant binding to make — which is precisely why they need their own narrow role
#: rather than the application's or the migration role's.
SessionDep = Annotated[AsyncSession, Depends(get_directory_session)]


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
async def user_by_email(
    session: SessionDep, request: Request, email: Annotated[str, Query()]
) -> dict[str, Any]:
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
    _trace_directory_read(request, user, "by-email")
    return await _serialise(session, user)


@router.get("/users/{user_id}", summary="One person, with every membership")
async def user_by_id(session: SessionDep, request: Request, user_id: uuid.UUID) -> dict[str, Any]:
    user = (
        await session.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundProblem("No such user")
    _trace_directory_read(request, user, "by-id")
    return await _serialise(session, user)


@router.get("/domains/{domain}", summary="Which organization owns an email domain")
async def organization_by_domain(session: SessionDep, domain: str) -> dict[str, Any]:
    """The verified-domain lookup single sign-on starts from (ADR-0000 §1).

    Returns the owning organization **and whether the domain has been verified**, rather than
    hiding an unverified row behind a 404. The caller needs the difference: "nobody has claimed
    this domain" and "somebody claimed it and never proved it" are the same answer to the person
    signing in and two very different lines in an operator's log. auth-service refuses both with
    one message and logs which it was.

    `verified` is what makes this safe to consult at all. A domain routes a sign-in to an
    identity provider, so honouring an unverified one would let anybody who can add a domain
    row intercept sign-ins for it — the account-takeover primitive `OrgDomain.verified_at`
    exists to prevent. This endpoint reports; auth-service decides, and refuses when it is
    false.

    Note what it does **not** decide: which organization the person is signed into. A verified
    domain selects an identity *provider*; the organization comes from the provider config that
    provider belongs to, and the sign-in still fails unless the person holds an active
    membership there.
    """
    row = (
        await session.execute(
            select(OrgDomain, Organization)
            .join(Organization, Organization.id == OrgDomain.organization_id)
            .where(OrgDomain.domain == domain, Organization.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        raise NotFoundProblem("No such domain")
    org_domain, organization = row
    return {
        "domain": str(org_domain.domain),
        "organization_id": str(organization.id),
        "organization_slug": organization.slug,
        "organization_status": organization.status,
        "verified": org_domain.verified_at is not None,
    }


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
    membership, grants = await selectors.get_membership_grants(session, membership_id)
    if membership is None:
        raise NotFoundProblem("No such membership")
    if membership.status != "active":
        return {"grants": [], "permission_version": 0}

    version = await current_permission_version(str(membership.organization_id))
    return {"grants": grants, "permission_version": version or 0}


def _trace_directory_read(request: Request, user: User, how: str) -> None:
    """Leave a trace of a cross-tenant directory read.

    These endpoints answer with every organization a person belongs to, across customers.
    That is the most sensitive read in the platform and until now it left no record at all.

    A structured log line rather than an `audit_logs` row, deliberately, for three reasons.
    There is **no organization to attribute the row to** — resolving somebody before an
    organization is known is the entire purpose of the lookup, and `audit_logs.organization_id`
    is NOT NULL. The caller is a **service**, so there is no actor to record either. And these
    are GET requests on a session that never commits, so a staged row would simply be
    discarded.

    `record_sensitive_read` remains the right tool for a *user* unmasking PII inside a tenant,
    where all three of those hold. It is the wrong tool here.

    The grants endpoint is deliberately **not** traced: both gateways call it on every
    authenticated request, so a line per call would bury the signal and cost the hottest path
    in the platform. Sign-in frequency is a different order of magnitude.

    The email is not logged — only the resolved user id. Logging the address would put the
    identifier being looked up into every log aggregator, which is the disclosure this is
    meant to make visible rather than duplicate.
    """
    log.info(
        "directory.cross_tenant_read",
        lookup=how,
        user_id=str(user.id),
        organizations=len(user.memberships),
        calling_service=request.headers.get("X-Calling-Service", "unknown"),
    )
