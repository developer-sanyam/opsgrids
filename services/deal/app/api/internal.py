"""The internal API — service-to-service, never reachable from outside the cluster.

One endpoint, for the deal-side half of lead conversion: `POST /leads/{id}:convert` on
lead-service needs a deal opened for the company (+ contact) it just ensured on customer-service,
and lead-service cannot open one itself — no cross-schema write, and no reason for lead-service
to hold deal-service's write permissions. It asks over the internal API under a short-lived
token addressed to deal by name, the same shape customer-service's `internal.py` already takes.

Mounted at the root and excluded from the OpenAPI document: not public API, and it must not
appear as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.schemas import LeadConversionDealIn, LeadConversionDealOut
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
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
    than opening a second one (ADR-0017's "one deal" half of the conversion guarantee). Opens
    at the `lead` stage with the pipeline's own default probability, an explicit `close_date`
    lead-service already computed, and `source="lead"` so the deal's own timeline shows where
    it came from.
    """
    async with session.begin():
        await _bind(session, organization_id, request)

        deal = await selectors.get_deal(session, organization_id, payload.deal_id)
        if deal is None:
            ctx = services.lead_conversion_ctx(organization_id)
            deal = await services.create_deal(
                session,
                ctx,
                name=payload.name,
                company_id=payload.company_id,
                primary_contact_id=payload.contact_id,
                owner_id=payload.owner_id,
                value_minor=0,
                close_date=payload.close_date,
                stage="lead",
                source=payload.source,
                deal_id=payload.deal_id,
            )

    log.info("deal.lead_conversion.ensured", deal_id=str(deal.id))
    return LeadConversionDealOut(deal_id=str(deal.id))
