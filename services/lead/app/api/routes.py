"""HTTP surface for the lead service — CRM's intake, ahead of the pipeline.

Mounted behind the gateway's `/api/v1/leads` prefix, so every route path here begins
`/leads/...`. Every route declares the permission it needs and is guarded by
`require_product("crm")` as defence in depth behind the gateway's own entitlement gate. Reads
go through `selectors`, writes through `services`; a router never builds its own query. Every
route takes the tenant-bound session and opens no transaction of its own — the chokepoint owns
it.

Object-level scope is re-applied on every detail route: filtering the list and forgetting the
detail is the named vulnerability, so a caller who can only see their own leads is refused
another's by id.

`/leads/assignment-rule` is declared before `/leads/{lead_id}` — routes match in declaration
order, and a static path always has to come first or it falls into the dynamic one.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    AssignmentRuleIn,
    AssignmentRuleOut,
    LeadConvertOut,
    LeadIn,
    LeadOut,
    LeadPatch,
    ServiceInfo,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_session, get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the crm scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers CRM scope questions
# for the rows it owns.
scope.register()

router = APIRouter(tags=["lead"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's
#: organization before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: `:convert` alone uses the raw, unwrapped session — `services.convert_lead`'s claim / call /
#: record shape needs three separate transactions on one session, with the calls to customer's
#: and deal's `/internal/v1` running between them with no transaction open at all. Binding the
#: whole route in `get_tenant_session`'s one request-long transaction (every other route here
#: does, and should) would hold that transaction open across two network round trips — the
#: transaction-in-a-transaction problem `services.py`'s own module docstring explains.
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]

#: The `crm` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("crm"))

LeadRead = Annotated[ScopeContext, Depends(require("crm.lead.lead.read"))]
LeadWrite = Annotated[ScopeContext, Depends(require("crm.lead.lead.write"))]
LeadConvert = Annotated[ScopeContext, Depends(require("crm.lead.lead.convert"))]


@router.get("/lead/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ assignment rule


@router.get(
    "/leads/assignment-rule",
    response_model=AssignmentRuleOut,
    summary="The organization's round-robin assignment rule",
    dependencies=[Entitled],
)
async def get_assignment_rule(session: SessionDep, ctx: LeadRead) -> AssignmentRuleOut:
    rule = await selectors.get_assignment_rule(session, ctx.organization_id)
    if rule is None:
        return AssignmentRuleOut(mode="round_robin", owner_ids=[], cursor=0)
    return selectors.assignment_rule_out(rule)


@router.put(
    "/leads/assignment-rule",
    response_model=AssignmentRuleOut,
    summary="Configure the round-robin (ORG scope only)",
    dependencies=[Entitled],
)
async def put_assignment_rule(
    payload: AssignmentRuleIn, session: SessionDep, ctx: LeadWrite
) -> AssignmentRuleOut:
    rule = await services.set_assignment_rule(
        session, ctx, mode=payload.mode, owner_ids=payload.owner_ids
    )
    return selectors.assignment_rule_out(rule)


# ------------------------------------------------------------------ leads


@router.get(
    "/leads",
    response_model=Page[LeadOut],
    summary="Leads in this organization's intake",
    dependencies=[Entitled],
)
async def list_leads(
    session: SessionDep,
    ctx: LeadRead,
    params: PageQuery,
    lead_status: Annotated[str | None, Query(alias="status")] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[LeadOut]:
    stmt = selectors.leads_stmt(ctx, status=lead_status, owner_id=owner_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.lead_out(lead) for lead in rows], total=total, params=params)


@router.post(
    "/leads",
    response_model=LeadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Capture a lead",
    dependencies=[Entitled],
)
async def create_lead(
    payload: LeadIn, session: SessionDep, ctx: LeadWrite, request: Request
) -> LeadOut:
    lead = await services.create_lead(
        session,
        ctx,
        full_name=payload.full_name,
        company=payload.company,
        email=payload.email,
        phone=payload.phone,
        source=payload.source,
        owner_id=payload.owner_id,
        notes=payload.notes,
        request=request,
    )
    return selectors.lead_out(lead)


@router.get(
    "/leads/{lead_id}",
    response_model=LeadOut,
    summary="One lead",
    dependencies=[Entitled],
)
async def read_lead(lead_id: uuid.UUID, session: SessionDep, ctx: LeadRead) -> LeadOut:
    lead = await selectors.get_lead(session, ctx.organization_id, lead_id)
    if lead is None:
        raise NotFoundProblem("That lead is not part of this organization")
    ctx.assert_can_touch(owner_id=lead.owner_id)
    return selectors.lead_out(lead)


@router.patch(
    "/leads/{lead_id}",
    response_model=LeadOut,
    summary="Update a lead",
    dependencies=[Entitled],
)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadPatch,
    session: SessionDep,
    ctx: LeadWrite,
    request: Request,
) -> LeadOut:
    """`services.update_lead` does its own row-locked fetch, the not-found check and the
    object-level scope check — it has to be the one holding the lock across all of it, so
    there is no separate unlocked pre-fetch here the way most other routes do."""
    updated = await services.update_lead(session, ctx, lead_id, payload, request=request)
    return selectors.lead_out(updated)


@router.post(
    "/leads/{lead_id}:convert",
    response_model=LeadConvertOut,
    status_code=status.HTTP_200_OK,
    summary="Convert a qualified lead into a company and a deal",
    dependencies=[Entitled],
)
async def convert_lead(
    lead_id: uuid.UUID,
    session: RawSessionDep,
    ctx: LeadConvert,
    request: Request,
) -> LeadConvertOut:
    """Idempotent: converting an already-converted lead returns the same ids rather than
    erroring or duplicating anything (`services.convert_lead`, `domain.conversion_ids`).

    Uses the raw session rather than the tenant chokepoint's one-transaction-per-request —
    `services.convert_lead` opens and binds its own three transactions (claim, then the two
    calls to customer's and deal's `/internal/v1` with no transaction open, then record), so
    no transaction should already be open when it is called. `NotFoundProblem` for an unknown
    lead and the OWN-scope object-level check both happen inside `services.claim_conversion`
    now, the same checks the old single-transaction version made here before calling in.
    """
    company_id, contact_id, deal_id = await services.convert_lead(
        session, ctx, lead_id, request=request
    )
    return LeadConvertOut(
        lead_id=str(lead_id),
        company_id=str(company_id),
        contact_id=str(contact_id) if contact_id else None,
        deal_id=str(deal_id),
    )
