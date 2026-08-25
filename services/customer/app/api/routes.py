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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors
from app.config import settings
from app.models import SCHEMA
from app.schemas import CompanyOut, ContactOut, OwnerOut, ServiceInfo
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the CRM scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers CRM scope questions.
scope.register()

router = APIRouter(tags=["customer"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's
#: organization before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `crm` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("crm"))

CompanyRead = Annotated[ScopeContext, Depends(require("crm.book.company.read"))]
ContactRead = Annotated[ScopeContext, Depends(require("crm.book.contact.read"))]


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
) -> Page[ContactOut]:
    stmt = selectors.contacts_stmt(ctx, q=q, status=contact_status)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.contact_out(c) for c in rows], total=total, params=params)


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
