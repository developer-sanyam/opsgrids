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

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    AssignmentRuleIn,
    AssignmentRuleOut,
    LeadConvertIn,
    LeadConvertOut,
    LeadIn,
    LeadOut,
    LeadPatch,
    LeadRevealIn,
    LeadRevealOut,
    LeadSource,
    ServiceInfo,
)
from platform_core.auth import CurrentPrincipal, ScopeContext, require, require_product
from platform_core.db import get_session, get_tenant_session
from platform_core.errors import ForbiddenProblem, NotFoundProblem
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
REVEAL_PERMISSION = "crm.lead.lead.read_sensitive"
#: Holding the permission is the whole gate: no step-up, no one-time code (owner decision
#: 2026-10-15, ADR-0023 addendum). What still constrains a reveal is the permission, the scope
#: applied to the one record, the audit row and the per-user reveal budget.
_require_reveal = require(REVEAL_PERMISSION)


async def _reveal_guard(principal: CurrentPrincipal, request: Request) -> ScopeContext:
    """The reveal route's authorization: the dangerous permission, and nothing else at the door.
    There is no step-up: a caller who holds `read_sensitive` reveals with one tap (owner decision
    2026-10-15, ADR-0023 addendum). The value stays masked until this route is called, the scope
    is re-applied to the one record, an audit row is written, and the budget is spent.

    It is `require(...)` itself, wrapped, so the rules are the platform's; the wrapper adds what
    `require` cannot: a refusal is logged (`services.log_reveal_refused`) — the permission
    check happens here, before the route body exists, so this is the only place that can
    see them. The per-user reveal budget is **not** spent here: it is spent in
    `services.reveal_lead_field`, after the request has been shown to be one that could reveal
    something (a real lead, in scope, with a value on file) and before anything is decrypted, so
    a token cannot burn the real person's budget with requests that were going to be refused.
    """
    try:
        ctx: ScopeContext = _require_reveal(principal)
    except ForbiddenProblem:
        # With no step-up in the guard, `require` refuses for one reason: the permission.
        services.log_reveal_refused(
            principal,
            reason="missing_permission",
            lead_id=_path_uuid(request, "lead_id"),
            request=request,
        )
        raise
    return ctx


def _path_uuid(request: Request, name: str) -> uuid.UUID | None:
    """The path parameter as a UUID for a log line, or None when it is not one — the
    parameter is client-supplied text and is only ever logged after it parses."""
    try:
        return uuid.UUID(str(request.path_params.get(name)))
    except ValueError:
        return None


LeadReveal = Annotated[ScopeContext, Depends(_reveal_guard)]


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
    source: Annotated[LeadSource | None, Query()] = None,
) -> Page[LeadOut]:
    stmt = selectors.leads_stmt(ctx, status=lead_status, owner_id=owner_id, source=source)
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
        fallback_owner_id=payload.fallback_owner_id,
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
    payload: Annotated[LeadConvertIn | None, Body()] = None,
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
    options = payload or LeadConvertIn()
    company_id, contact_id, deal_id = await services.convert_lead(
        session,
        ctx,
        lead_id,
        deal_value_minor=options.deal_value_minor,
        deal_stage=options.deal_stage,
        request=request,
    )
    return LeadConvertOut(
        lead_id=str(lead_id),
        company_id=str(company_id),
        contact_id=str(contact_id) if contact_id else None,
        deal_id=str(deal_id),
    )


@router.post(
    "/leads/{lead_id}:reveal",
    response_model=LeadRevealOut,
    status_code=status.HTTP_200_OK,
    summary="Reveal a lead's real phone number or email (audited)",
    dependencies=[Entitled],
    responses={
        403: {
            "description": (
                "Missing `crm.lead.lead.read_sensitive` (no step-up or one-time code is "
                "needed when it is held), or the "
                "lead is outside the caller's scope."
            )
        },
        404: {
            "description": (
                "The lead is not part of this organization, or it has no value on file for "
                "the requested field."
            )
        },
        429: {
            "description": (
                "This user has spent their reveal budget in this organization: 60 per 10 "
                "minutes or 600 per day, shared with the contact reveal. Only a request that "
                "would have revealed a value spends from it. `Retry-After` is the length of "
                "the window that was spent, in seconds."
            )
        },
        503: {
            "description": (
                "The budget could not be checked (Redis is unreachable), so nothing is "
                "revealed. Retry shortly."
            )
        },
    },
)
async def reveal_lead_field(
    lead_id: uuid.UUID,
    payload: LeadRevealIn,
    ctx: LeadReveal,
    session: SessionDep,
    request: Request,
    response: Response,
) -> LeadRevealOut:
    """The only public route that returns a lead's real phone number or email.

    A POST rather than a GET on purpose: it has a side effect — it writes an
    `entity.sensitive_read` audit row naming the field — and it must never be cached,
    pre-fetched, logged in a query string or replayed from browser history the way a GET can
    be. The response says so itself (`Cache-Control: no-store`).

    Guarded by the dangerous `read_sensitive` permission alone (`_reveal_guard`; no step-up,
    ADR-0023 addendum), then re-scoped to this one lead inside `services.reveal_lead_field`, which
    also owns the
    not-found, the nothing-on-file, the per-user reveal budget and the audit steps. This route
    takes the ordinary tenant session, so the audit row commits before the response is sent.
    """
    value = await services.reveal_lead_field(session, ctx, lead_id, payload.field, request=request)
    response.headers["Cache-Control"] = "no-store"
    return LeadRevealOut(lead_id=str(lead_id), field=payload.field, value=value)
