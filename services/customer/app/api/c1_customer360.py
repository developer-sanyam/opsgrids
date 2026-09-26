"""`GET /customers/{id}/360` — one contact's whole picture (slice C1).

Mounted behind the gateway's existing `/api/v1/customers` prefix. `id` is a **contact** id: the
360 is a person's card (masked phone and email, messaging standing), with their account alongside.

Authorization is the same as reading the contact: `crm.book.contact.read`, the `crm` entitlement,
and the object-level scope check, so a rep limited to their own book gets a 404-equivalent 403 for
someone else's contact exactly as `GET /contacts/{id}` does. A contact in another organization is
a 404, never a 403 — confirming an id exists elsewhere is itself a disclosure.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import c1_360, scope, selectors
from app.c1_schemas import Customer360
from app.config import settings
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem

scope.register()

router = APIRouter(tags=["customer"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("crm"))
ContactRead = Annotated[ScopeContext, Depends(require("crm.book.contact.read"))]


@router.get(
    "/customers/{contact_id}/360",
    response_model=Customer360,
    summary="One contact across the platform",
    dependencies=[Entitled],
)
async def read_customer_360(
    contact_id: uuid.UUID, request: Request, session: SessionDep, ctx: ContactRead
) -> Customer360:
    """Sections that cannot be produced are `null` and named in `unavailable`; the response is
    still 200. Only the contact itself decides 404."""
    org = ctx.organization_id
    contact = await selectors.get_contact(session, org, contact_id)
    if contact is None:
        raise NotFoundProblem("That contact is not part of this organization")
    ctx.assert_can_touch(owner_id=contact.owner_id)

    company = await selectors.get_company(session, org, contact.company_id)
    owner = await selectors.get_owner(session, org, contact.owner_id)

    caller = c1_360.CallerContext(
        organization_id=org,
        user_id=ctx.principal.sub,
        principal=ctx.principal,
        forward_headers=c1_360.forwarded_identity(request.headers),
    )
    return await c1_360.assemble(settings(), caller, contact=contact, company=company, owner=owner)
