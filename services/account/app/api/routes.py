"""HTTP surface for the account service.

Every route declares the permission it needs, and the routes that have no meaningful
"own" reading declare a **scope floor**. That distinction is the security boundary:
everyone holds `core.identity.membership.write` at OWN so they can edit themselves, so a
route guarded only by the permission *name* would let any member invite and remove people.

Reads go through `selectors`, writes through `services`. A router that builds its own query
is how an organization leaks, so none of them do.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    InvitationAccept,
    InvitationCreated,
    InvitationIn,
    InvitationOut,
    LocationIn,
    LocationOut,
    MemberOut,
    MemberPatch,
    OrganizationOut,
    OrganizationPatch,
    OrgUnitIn,
    OrgUnitNode,
    OrgUnitOut,
    PermissionOut,
    RoleAssignment,
    RoleIn,
    RoleOut,
    ServiceInfo,
)
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["account"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# --- read contexts ------------------------------------------------------------------
OrgRead = Annotated[ScopeContext, Depends(require("core.org.organization.read"))]
UnitRead = Annotated[ScopeContext, Depends(require("core.org.unit.read"))]
LocationRead = Annotated[ScopeContext, Depends(require("core.org.location.read"))]
MemberRead = Annotated[ScopeContext, Depends(require("core.identity.membership.read"))]
RoleRead = Annotated[ScopeContext, Depends(require("core.identity.role.read"))]

# --- write contexts -----------------------------------------------------------------
# Each of these carries `minimum=Scope.ORG`. Administering the organization has no "own"
# reading, and every member holds the OWN grant for self-service.
OrgWrite = Annotated[
    ScopeContext, Depends(require("core.org.organization.write", minimum=Scope.ORG))
]
UnitWrite = Annotated[ScopeContext, Depends(require("core.org.unit.write", minimum=Scope.ORG))]
LocationWrite = Annotated[
    ScopeContext, Depends(require("core.org.location.write", minimum=Scope.ORG))
]
MemberWrite = Annotated[
    ScopeContext, Depends(require("core.identity.membership.write", minimum=Scope.ORG))
]
RoleWrite = Annotated[ScopeContext, Depends(require("core.identity.role.write", minimum=Scope.ORG))]


@router.get("/account/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# --------------------------------------------------------------------- organization


@router.get("/organization", response_model=OrganizationOut, summary="The caller's organization")
async def read_organization(session: SessionDep, ctx: OrgRead) -> OrganizationOut:
    org = await selectors.get_organization(session, ctx.organization_id)
    if org is None:
        raise NotFoundProblem("Organization not found")
    return OrganizationOut.model_validate(org)


@router.patch("/organization", response_model=OrganizationOut, summary="Update the organization")
async def update_organization(
    payload: OrganizationPatch, session: SessionDep, ctx: OrgWrite
) -> OrganizationOut:
    async with session.begin():
        org = await selectors.get_organization(session, ctx.organization_id)
        if org is None:
            raise NotFoundProblem("Organization not found")
        updated = await services.update_organization(session, ctx, org, payload)
        return OrganizationOut.model_validate(updated)


# --------------------------------------------------------------------- structure


@router.get("/org-units", response_model=list[OrgUnitNode], summary="The department tree")
async def list_org_units(session: SessionDep, ctx: UnitRead) -> list[OrgUnitNode]:
    units = await selectors.list_org_units(session, ctx.organization_id)
    return selectors.build_tree(units)


@router.post(
    "/org-units",
    response_model=OrgUnitOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a department",
)
async def create_org_unit(payload: OrgUnitIn, session: SessionDep, ctx: UnitWrite) -> OrgUnitOut:
    async with session.begin():
        unit = await services.create_org_unit(session, ctx, payload)
        return OrgUnitOut.model_validate(unit)


@router.post(
    "/org-units/{unit_id}/move", response_model=OrgUnitOut, summary="Re-parent a department"
)
async def move_org_unit(
    unit_id: uuid.UUID,
    session: SessionDep,
    ctx: UnitWrite,
    parent_id: Annotated[uuid.UUID | None, Query()] = None,
) -> OrgUnitOut:
    async with session.begin():
        unit = await selectors.get_org_unit(session, ctx.organization_id, unit_id)
        if unit is None:
            raise NotFoundProblem("Department not found")
        moved = await services.move_org_unit(session, ctx, unit, parent_id)
        return OrgUnitOut.model_validate(moved)


@router.get("/locations", response_model=list[LocationOut], summary="Sites")
async def list_locations(session: SessionDep, ctx: LocationRead) -> list[LocationOut]:
    rows = await selectors.list_locations(session, ctx.organization_id)
    return [LocationOut.model_validate(r) for r in rows]


@router.post(
    "/locations",
    response_model=LocationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a site",
)
async def create_location(
    payload: LocationIn, session: SessionDep, ctx: LocationWrite
) -> LocationOut:
    async with session.begin():
        location = await services.create_location(session, ctx, payload)
        return LocationOut.model_validate(location)


# --------------------------------------------------------------------- members


@router.get("/members", response_model=Page[MemberOut], summary="People in the organization")
async def list_members(
    session: SessionDep,
    ctx: MemberRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, email or job title")] = None,
    org_unit_id: Annotated[uuid.UUID | None, Query()] = None,
    member_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[MemberOut]:
    stmt = selectors.members_stmt(ctx, q=q, org_unit_id=org_unit_id, status=member_status)
    # `paginate` scalars() to the first entity, so page the memberships and read the
    # joined user off the relationship rather than losing the second column.
    rows, total = await paginate(session, stmt, params)
    items = [selectors.to_member_out(m, m.user) for m in rows]
    return Page.build(items, total=total, params=params)


@router.get("/members/{membership_id}", response_model=MemberOut, summary="One person")
async def read_member(membership_id: uuid.UUID, session: SessionDep, ctx: MemberRead) -> MemberOut:
    found = await selectors.get_member(session, ctx.organization_id, membership_id)
    if found is None:
        raise NotFoundProblem("That person is not a member of this organization")
    membership, user = found
    # Filtering the list and forgetting the detail is the named vulnerability. This is
    # the guard that closes it.
    ctx.assert_can_touch(
        owner_id=membership.id,
        team_owner_id=membership.manager_membership_id,
        location_code=next(iter(selectors.split_codes(membership.location_codes)), None),
    )
    return selectors.to_member_out(membership, user)


@router.patch("/members/{membership_id}", response_model=MemberOut, summary="Update a person")
async def update_member(
    membership_id: uuid.UUID, payload: MemberPatch, session: SessionDep, ctx: MemberWrite
) -> MemberOut:
    async with session.begin():
        found = await selectors.get_member(session, ctx.organization_id, membership_id)
        if found is None:
            raise NotFoundProblem("That person is not a member of this organization")
        membership, user = found
        updated = await services.update_member(session, ctx, membership, payload)
        return selectors.to_member_out(updated, user)


@router.delete(
    "/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove somebody from the organization",
)
async def revoke_member(
    membership_id: uuid.UUID,
    session: SessionDep,
    ctx: MemberWrite,
    reason: Annotated[str | None, Query()] = None,
) -> Response:
    async with session.begin():
        found = await selectors.get_member(session, ctx.organization_id, membership_id)
        if found is None:
            raise NotFoundProblem("That person is not a member of this organization")
        await services.revoke_member(session, ctx, found[0], reason=reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/members/{membership_id}/roles", response_model=MemberOut, summary="Set roles")
async def set_member_roles(
    membership_id: uuid.UUID,
    payload: RoleAssignment,
    session: SessionDep,
    ctx: RoleWrite,
) -> MemberOut:
    async with session.begin():
        found = await selectors.get_member(session, ctx.organization_id, membership_id)
        if found is None:
            raise NotFoundProblem("That person is not a member of this organization")
        membership, user = found
        updated = await services.set_member_roles(session, ctx, membership, payload.role_codes)
        return selectors.to_member_out(updated, user)


# --------------------------------------------------------------------- roles


@router.get("/roles", response_model=list[RoleOut], summary="Roles")
async def list_roles(session: SessionDep, ctx: RoleRead) -> list[RoleOut]:
    rows = await selectors.list_roles(session, ctx.organization_id)
    return [selectors.to_role_out(r) for r in rows]


@router.post(
    "/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED, summary="Create a role"
)
async def create_role(payload: RoleIn, session: SessionDep, ctx: RoleWrite) -> RoleOut:
    async with session.begin():
        role = await services.create_role(session, ctx, payload)
        await session.flush()
        return selectors.to_role_out(role)


@router.put("/roles/{role_id}", response_model=RoleOut, summary="Replace a role's permissions")
async def replace_role_grants(
    role_id: uuid.UUID, payload: RoleIn, session: SessionDep, ctx: RoleWrite
) -> RoleOut:
    async with session.begin():
        role = await selectors.get_role(session, ctx.organization_id, role_id)
        if role is None:
            raise NotFoundProblem("Role not found")
        updated = await services.replace_role_grants(session, ctx, role, payload.grants)
        await session.flush()
        return selectors.to_role_out(updated)


@router.get("/permissions", response_model=list[PermissionOut], summary="Permission catalogue")
async def list_permissions(
    session: SessionDep,
    ctx: RoleRead,
    product: Annotated[str | None, Query()] = None,
) -> list[PermissionOut]:
    rows = await selectors.list_permissions(session, product)
    return [PermissionOut.model_validate(r) for r in rows]


# --------------------------------------------------------------------- invitations


@router.get("/invitations", response_model=list[InvitationOut], summary="Pending invitations")
async def list_invitations(session: SessionDep, ctx: MemberRead) -> list[InvitationOut]:
    rows = await selectors.list_invitations(session, ctx.organization_id)
    return [InvitationOut.model_validate(r) for r in rows]


@router.post(
    "/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Invite somebody",
)
async def create_invitation(
    payload: InvitationIn, session: SessionDep, ctx: MemberWrite
) -> InvitationCreated:
    async with session.begin():
        invitation, raw = await services.invite(session, ctx, payload)
        # The only moment the raw token exists outside the email that delivers it.
        return InvitationCreated(**InvitationOut.model_validate(invitation).model_dump(), token=raw)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID, session: SessionDep, ctx: MemberWrite
) -> Response:
    async with session.begin():
        rows = await selectors.list_invitations(session, ctx.organization_id, pending_only=False)
        invitation = next((i for i in rows if i.id == invitation_id), None)
        if invitation is None:
            raise NotFoundProblem("Invitation not found")
        await services.revoke_invitation(session, ctx, invitation)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invitations/accept",
    response_model=MemberOut,
    summary="Accept an invitation",
)
async def accept_invitation(payload: InvitationAccept, session: SessionDep) -> MemberOut:
    """Public by design: the invitee has a token, not yet an account.

    The token is the credential, so every failure — unknown, expired, already used —
    returns the same message. A distinct "expired" reply confirms to whoever is guessing
    that they found a real one.
    """
    async with session.begin():
        membership = await services.accept_invitation(session, payload.token, payload.full_name)
        await session.flush()
        found = await selectors.get_member(session, membership.organization_id, membership.id)
        if found is None:  # pragma: no cover - defensive
            raise NotFoundProblem("Membership not found")
        return selectors.to_member_out(found[0], found[1])
