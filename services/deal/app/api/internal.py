"""The internal API — service-to-service, never reachable from outside the cluster.

One endpoint, for the deal-side half of lead conversion: `POST /leads/{id}:convert` on
lead-service needs a deal opened for the company (+ contact) it just ensured on customer-service,
and lead-service cannot open one itself — no cross-schema write, and no reason for lead-service
to hold deal-service's write permissions. It asks over the internal API under a short-lived
token addressed to deal by name, the same shape customer-service's `internal.py` already takes.

Restricted to `service_caller == "lead"` (`_require_caller`) and to a token whose `org` claim
names the path's organization (`_require_org_claim`) — a valid signature proves *which service*
minted the token, never *which route it was meant for* or *which tenant*, and both are separate
checks here.

**This route trusts `payload.owner_id` is an active rep without checking.** Deal-service has no
Owner table of its own — that data lives in customer's schema, cross-schema reads are not a
thing this platform does, and customer's own `owners:validate` internal route is deliberately
restricted to lead-service alone (`services/customer/app/api/internal.py`), not opened to deal
too. The enforcement point is upstream: lead-service validates `owner_id` against customer's
directory before it ever calls *either* customer or deal (`app/services.convert_lead` in
lead-service), so by the time a request reaches here the owner has already been confirmed
active — this route is not where that check could usefully live even if it wanted to.

Mounted at the root and excluded from the OpenAPI document: not public API, and it must not
appear as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.schemas import LeadConversionDealIn, LeadConversionDealOut, ReferencesOut
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import ForbiddenProblem
from platform_core.logging import get_logger

log = get_logger("deal.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _bind(session: AsyncSession, organization_id: uuid.UUID, request: Request) -> None:
    """Bind this transaction to the caller's organization — the internal analogue of the
    tenancy chokepoint. Must run as the first statement inside the transaction."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


def _require_caller(request: Request, *expected: str) -> None:
    """Restrict an internal route to a named allow-list of calling services, on top of the
    valid-signature check `require_service_token` already did — the same guard
    customer-service's `internal.py` applies to its own lead-conversion route."""
    caller = getattr(request.state, "service_caller", "unknown-service")
    if caller not in expected:
        raise ForbiddenProblem(f"{caller!r} may not call this endpoint")


def _require_org_claim(request: Request, organization_id: uuid.UUID) -> None:
    """The token's own `org` claim must name the organization in the path — see
    customer-service's identical check for why the signature and audience alone are not
    enough."""
    claims = getattr(request.state, "service_claims", None) or {}
    if claims.get("org") != str(organization_id):
        raise ForbiddenProblem("This token is not addressed to this organization")


def _claimed_actor(request: Request) -> uuid.UUID | None:
    """The human `sub` the caller's token carried, if any — `lead_conversion_ctx`'s actor."""
    claims = getattr(request.state, "service_claims", None) or {}
    sub = claims.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(str(sub))
    except ValueError:
        return None


@router.post(
    "/organizations/{organization_id}/leads:convert",
    response_model=LeadConversionDealOut,
    status_code=status.HTTP_200_OK,
    summary="Ensure a deal exists for a converting lead",
)
async def convert_lead(
    organization_id: uuid.UUID,
    payload: LeadConversionDealIn,
    session: SessionDep,
    request: Request,
) -> LeadConversionDealOut:
    """The deal-side half of `POST /leads/{id}:convert`.

    Idempotent by the caller's own `deal_id` — lead-service derives it deterministically from
    the lead's own id, so calling this twice with the same lead reuses the same deal rather
    than opening a second one (ADR-0017's "one deal" half of the conversion guarantee), even
    if the first attempt's deal was soft-deleted since (`get_deal_by_id_any_state` — a second
    `INSERT` with that id would otherwise fail against the row Postgres still holds). Opens at
    the organization's **entry stage** — the first active open stage of its pipeline, with that
    stage's default probability — an explicit `close_date` lead-service already computed, and
    `source="lead"` so the deal's own timeline shows where it came from. A pipeline with no
    active open stage answers 422 (`no_entry_stage`), which lead's client turns into a permanent
    rejection, never into the "name conflict" a 409 would be read as.
    """
    _require_caller(request, "lead")
    _require_org_claim(request, organization_id)

    async with session.begin():
        await _bind(session, organization_id, request)

        deal = await selectors.get_deal_by_id_any_state(session, organization_id, payload.deal_id)
        if deal is None:
            ctx = services.lead_conversion_ctx(organization_id, _claimed_actor(request))
            name = await services.unique_deal_name(session, organization_id, payload.name)
            deal = await services.create_deal(
                session,
                ctx,
                name=name,
                company_id=payload.company_id,
                primary_contact_id=payload.contact_id,
                owner_id=payload.owner_id,
                value_minor=payload.value_minor,
                stage=payload.stage,
                close_date=payload.close_date,
                source=payload.source,
                deal_id=payload.deal_id,
                acting_as="lead",
            )

    log.info("deal.lead_conversion.ensured", deal_id=str(deal.id))
    return LeadConversionDealOut(deal_id=str(deal.id))


@router.get(
    "/organizations/{organization_id}/references",
    response_model=ReferencesOut,
    summary="Count the deals and activities that name a company or contact",
)
async def references(
    organization_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    company_id: Annotated[uuid.UUID, Query()],
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    exclude_deal_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ReferencesOut:
    """Customer's probe before it deletes an aborted conversion's orphan company or contact.

    `exclude_deal_id` (the aborted attempt's own deal) is left out of the deal counts only when
    deal would itself treat it as disposable: absent or soft-deleted (not counted anyway), or
    live and `is_pristine_lead_deal`. A deal deal keeps still names the company, so it still
    counts. An unknown id counts zero; there is no 404. Counts only, never ids."""
    _require_caller(request, "customer")
    _require_org_claim(request, organization_id)

    async with session.begin():
        await _bind(session, organization_id, request)
        skip = None
        if exclude_deal_id is not None and await services.is_pristine_lead_deal(
            session, organization_id, exclude_deal_id
        ):
            skip = exclude_deal_id
        counts = await selectors.reference_counts(
            session,
            organization_id,
            company_id=company_id,
            contact_id=contact_id,
            skip_deal_id=skip,
        )

    log.info("deal.references.counted", company_id=str(company_id))
    return ReferencesOut(
        company_deal_count=counts[0],
        contact_deal_count=counts[1],
        company_activity_count=counts[2],
        contact_activity_count=counts[3],
    )
