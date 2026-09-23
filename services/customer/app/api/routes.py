"""HTTP surface for the customer service — the CRM account book.

Mounted behind the gateway's `/api/v1/customers` and `/api/v1/contacts` prefixes, so the
route paths here begin `/customers/...` and `/contacts/...`. The read endpoints are what
flips the CRM SPA from fixtures to live, and each returns exactly the shape
`frontend/product-crm/src/api/types.ts` declares (the SPA's `/companies` and `/owners`
calls map onto `/customers` and `/customers/owners` — the entity is the same, the gateway
prefix is already routed).

Every route declares the permission it needs and is guarded by `require_product("crm")`
as defence in depth behind the gateway's own entitlement gate. Reads go through
`selectors`; a router never builds its own query. Every route takes the tenant-bound
session and opens no transaction of its own — the chokepoint owns it.

Object-level scope is re-applied on every detail route: filtering the list and forgetting
the detail is the named vulnerability, so a caller who can only list their own accounts is
refused another's by id.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    CompanyIn,
    CompanyOut,
    CompanyPatch,
    ContactIn,
    ContactOut,
    ContactPatch,
    ContactPhoneIn,
    ContactPhoneRevealed,
    OwnerIn,
    OwnerOut,
    OwnerPatch,
    ServiceInfo,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the CRM scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers CRM scope questions.
scope.register()

router = APIRouter(tags=["customer"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's
#: organization before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `crm` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("crm"))

CompanyRead = Annotated[ScopeContext, Depends(require("crm.book.company.read"))]
CompanyWrite = Annotated[ScopeContext, Depends(require("crm.book.company.write"))]
CompanyDelete = Annotated[ScopeContext, Depends(require("crm.book.company.delete"))]
#: Reference data for the whole organization — there is no rep who owns the list of reps —
#: so the floor is ORG; an OWN grant is refused outright rather than silently widened.
OwnerWrite = Annotated[ScopeContext, Depends(require("crm.book.owner.write", minimum=Scope.ORG))]
ContactRead = Annotated[ScopeContext, Depends(require("crm.book.contact.read"))]
ContactWrite = Annotated[ScopeContext, Depends(require("crm.book.contact.write"))]
ContactDelete = Annotated[ScopeContext, Depends(require("crm.book.contact.delete"))]
#: Revealing a real phone number. The dangerous permission **and** a second factor, the
#: same pairing employee-service requires to unmask an employee's personal details — an
#: ordinary CRM session must not be a route to a customer's phone book.
ContactUnmask = Annotated[
    ScopeContext,
    Depends(require("crm.book.contact.read_sensitive", amr={"otp"})),
]


@router.get("/customer/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ companies

# NOTE: static paths (`/customers/owners`) are declared before the dynamic detail route;
# Starlette matches in declaration order, so `owners` never falls into `{company_id}`.


@router.get(
    "/customers",
    response_model=Page[CompanyOut],
    summary="Companies in the account book",
    dependencies=[Entitled],
)
async def list_companies(
    session: SessionDep,
    ctx: CompanyRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, industry or city")] = None,
    company_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[CompanyOut]:
    stmt = selectors.companies_stmt(ctx, q=q, status=company_status)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.company_out(c) for c in rows], total=total, params=params)


@router.post(
    "/customers",
    response_model=CompanyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a company to the account book",
    dependencies=[Entitled],
)
async def create_company(
    payload: CompanyIn, session: SessionDep, ctx: CompanyWrite, request: Request
) -> CompanyOut:
    company = await services.create_company(
        session,
        ctx,
        name=payload.name,
        owner_id=payload.owner_id,
        domain=payload.domain,
        industry=payload.industry,
        size_band=payload.size_band,
        city=payload.city,
        country=payload.country,
        status=payload.status,
        annual_revenue_minor=payload.annual_revenue_minor,
        currency=payload.currency,
        request=request,
    )
    return selectors.company_out(company)


@router.get(
    "/customers/owners",
    response_model=list[OwnerOut],
    summary="The rep directory — who owner_id values resolve to",
    dependencies=[Entitled],
)
async def list_owners(session: SessionDep, ctx: CompanyRead) -> list[OwnerOut]:
    """A plain array, not a page: the SPA loads the whole directory once and resolves
    `owner_id` values client-side. Reference data within the organization, so it is
    org-scoped but not scope-narrowed — a name must resolve on any row a caller sees."""
    reps = await selectors.owners(session, ctx.organization_id)
    return [selectors.owner_out(o) for o in reps]


@router.post(
    "/customers/owners",
    response_model=OwnerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a rep to the directory",
    dependencies=[Entitled],
)
async def create_owner(payload: OwnerIn, session: SessionDep, ctx: OwnerWrite) -> OwnerOut:
    """The prerequisite for everything else: a production org starts with zero owners,
    and every company / contact / deal create needs an `owner_id` that resolves to one."""
    owner = await services.create_owner(
        session, ctx, full_name=payload.full_name, email=payload.email, role=payload.role
    )
    return selectors.owner_out(owner)


@router.patch(
    "/customers/owners/{owner_id}",
    response_model=OwnerOut,
    summary="Edit a rep, or deactivate one",
    dependencies=[Entitled],
)
async def update_owner(
    owner_id: uuid.UUID,
    payload: OwnerPatch,
    session: SessionDep,
    ctx: OwnerWrite,
) -> OwnerOut:
    """One route for both edits and deactivation, distinguished by whether `is_active` is
    present. Deactivating refuses (409) while the rep still owns a live company or contact —
    reassign first. Reactivating has no such guard: a returning rep starts with an empty
    book."""
    owner = await selectors.get_owner(session, ctx.organization_id, owner_id)
    if owner is None:
        raise NotFoundProblem("That owner is not part of this organization")

    if payload.is_active is False:
        owner = await services.deactivate_owner(session, ctx, owner)
    elif payload.is_active is True:
        owner = await services.reactivate_owner(session, ctx, owner)

    owner = await services.update_owner(session, ctx, owner, payload)
    return selectors.owner_out(owner)


@router.get(
    "/customers/{company_id}",
    response_model=CompanyOut,
    summary="One company",
    dependencies=[Entitled],
)
async def read_company(company_id: uuid.UUID, session: SessionDep, ctx: CompanyRead) -> CompanyOut:
    company = await selectors.get_company(session, ctx.organization_id, company_id)
    if company is None:
        raise NotFoundProblem("That company is not part of this organization")
    ctx.assert_can_touch(owner_id=company.owner_id)
    return selectors.company_out(company)


@router.patch(
    "/customers/{company_id}",
    response_model=CompanyOut,
    summary="Update a company",
    dependencies=[Entitled],
)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyPatch,
    session: SessionDep,
    ctx: CompanyWrite,
    request: Request,
) -> CompanyOut:
    company = await selectors.get_company(session, ctx.organization_id, company_id)
    if company is None:
        raise NotFoundProblem("That company is not part of this organization")
    ctx.assert_can_touch(owner_id=company.owner_id)
    updated = await services.update_company(session, ctx, company, payload, request=request)
    return selectors.company_out(updated)


@router.delete(
    "/customers/{company_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a company",
    dependencies=[Entitled],
)
async def delete_company(
    company_id: uuid.UUID, session: SessionDep, ctx: CompanyDelete, request: Request
) -> Response:
    company = await selectors.get_company(session, ctx.organization_id, company_id)
    if company is None:
        raise NotFoundProblem("That company is not part of this organization")
    ctx.assert_can_touch(owner_id=company.owner_id)
    await services.delete_company(session, ctx, company, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ contacts


@router.get(
    "/contacts",
    response_model=Page[ContactOut],
    summary="Contacts across the account book",
    dependencies=[Entitled],
)
async def list_contacts(
    session: SessionDep,
    ctx: ContactRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, title or email")] = None,
    contact_status: Annotated[str | None, Query(alias="status")] = None,
    company_id: Annotated[
        uuid.UUID | None, Query(description="One account's contacts only")
    ] = None,
) -> Page[ContactOut]:
    stmt = selectors.contacts_stmt(ctx, q=q, status=contact_status, company_id=company_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.contact_out(c) for c in rows], total=total, params=params)


@router.post(
    "/contacts",
    response_model=ContactOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a contact to an account",
    dependencies=[Entitled],
)
async def create_contact(
    payload: ContactIn, session: SessionDep, ctx: ContactWrite, request: Request
) -> ContactOut:
    contact = await services.create_contact(
        session,
        ctx,
        full_name=payload.full_name,
        email=payload.email,
        company_id=payload.company_id,
        owner_id=payload.owner_id,
        title=payload.title,
        status=payload.status,
        raw_phone=payload.raw_phone,
        request=request,
    )
    return selectors.contact_out(contact)


@router.get(
    "/contacts/{contact_id}",
    response_model=ContactOut,
    summary="One contact",
    dependencies=[Entitled],
)
async def read_contact(contact_id: uuid.UUID, session: SessionDep, ctx: ContactRead) -> ContactOut:
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("That contact is not part of this organization")
    ctx.assert_can_touch(owner_id=contact.owner_id)
    return selectors.contact_out(contact)


@router.patch(
    "/contacts/{contact_id}",
    response_model=ContactOut,
    summary="Update a contact",
    dependencies=[Entitled],
)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactPatch,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ContactOut:
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("That contact is not part of this organization")
    ctx.assert_can_touch(owner_id=contact.owner_id)
    updated = await services.update_contact(session, ctx, contact, payload, request=request)
    return selectors.contact_out(updated)


@router.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a contact",
    dependencies=[Entitled],
)
async def delete_contact(
    contact_id: uuid.UUID, session: SessionDep, ctx: ContactDelete, request: Request
) -> Response:
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("That contact is not part of this organization")
    ctx.assert_can_touch(owner_id=contact.owner_id)
    await services.delete_contact(session, ctx, contact, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/contacts/{contact_id}/phone",
    response_model=ContactOut,
    summary="Set a contact's real phone number",
    dependencies=[Entitled],
)
async def set_contact_phone(
    contact_id: uuid.UUID,
    payload: ContactPhoneIn,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ContactOut:
    """Seal a real number onto a contact and derive its mask.

    Returns the ordinary `ContactOut` — the masked form — so the write surface cannot be used
    as a read-back oracle for the number just supplied. Revealing it needs the separate
    stepped-up route below.
    """
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("That contact is not part of this organization")
    ctx.assert_can_touch(owner_id=contact.owner_id)
    contact = await services.set_contact_phone(
        session, ctx, contact, payload.raw_phone, request=request
    )
    return selectors.contact_out(contact)


@router.post(
    "/contacts/{contact_id}/phone:reveal",
    response_model=ContactPhoneRevealed,
    status_code=status.HTTP_200_OK,
    summary="Reveal a contact's real phone number (audited)",
    dependencies=[Entitled],
)
async def reveal_contact_phone(
    contact_id: uuid.UUID,
    session: SessionDep,
    ctx: ContactUnmask,
    request: Request,
) -> ContactPhoneRevealed:
    """The only public route that returns a real number.

    A POST rather than a GET on purpose: this has a side effect — it writes an
    `entity.sensitive_read` audit row — and it must never be cached, pre-fetched, logged in a
    query string or replayed from browser history the way a GET can be.

    Guarded by the dangerous `read_sensitive` permission and a second factor, then re-scoped
    to this one contact. Filtering a list and forgetting the detail route is the named
    vulnerability this shape exists to avoid.
    """
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("That contact is not part of this organization")
    ctx.assert_can_touch(owner_id=contact.owner_id)
    phone = await services.unmask_contact_phone(session, ctx, contact, request=request)
    return ContactPhoneRevealed(contact_id=str(contact.id), phone=phone)
