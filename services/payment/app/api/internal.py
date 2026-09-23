"""The internal API — service-to-service, never reachable from outside the cluster.

One endpoint: the charge billing raises after it finalizes an invoice. There is no person and no
gateway-forwarded principal — the caller is another service — so it does not take
`get_tenant_session`; it binds the transaction to the **organization named in the body** as its
first statement, exactly as `get_tenant_session` binds to a principal's organization, so RLS and
every `WITH CHECK` apply to the write.

Guarded by a short-lived service token addressed to this service by name (`require_service_token`).
Mounted at the root and excluded from the OpenAPI document: not public API, and it must not appear
as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.deps import get_provider
from app.providers.base import PaymentProvider
from app.schemas import ChargeIn, ChargeOut, UsableMandateOut
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import ForbiddenProblem
from platform_core.logging import get_logger

log = get_logger("payment.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[PaymentProvider, Depends(get_provider)]


@router.post(
    "/charges",
    response_model=ChargeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a charge for an invoice — billing's post-finalize delegate",
)
async def create_charge(
    payload: ChargeIn, session: SessionDep, request: Request, provider: ProviderDep
) -> ChargeOut:
    """Raise a charge, idempotently on the platform key. The transaction is bound to the payload's
    organization as its first statement; RLS then isolates the write."""
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        charge = await services.create_charge(session, provider, payload, request=request)
        result = ChargeOut.model_validate(charge)
    log.info(
        "payment.charge.created",
        charge_id=str(result.id),
        organization_id=str(payload.organization_id),
        state=result.state,
    )
    return result


#: Who may ask whether an organization can be debited. subscription's billing run asks it when a
#: trial ends (ADR-0013 §6); nothing else has a reason to probe a customer's payment standing.
MANDATE_READERS = frozenset({"subscription"})


@router.get(
    "/organizations/{organization_id}/mandates:usable",
    response_model=UsableMandateOut,
    summary="Could this organization be debited this amount now? — the trial-end question",
)
async def usable_mandate(
    organization_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    currency: Annotated[str, Query(min_length=3, max_length=3)],
    amount_minor: Annotated[int, Query(ge=0)],
) -> UsableMandateOut:
    """Answers with identifiers only. The transaction is bound to the organization in the path as
    its first statement, so another organization's mandates are invisible under RLS as well as
    filtered out by the selector."""
    caller = getattr(request.state, "service_caller", None)
    if caller not in MANDATE_READERS:
        raise ForbiddenProblem("this service may not read payment standing")
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, organization_id)
        if not settings.is_production:
            await assert_tenant(session, organization_id)
        mandate = await selectors.usable_mandate(
            session, organization_id, currency=currency, amount_minor=amount_minor
        )
    return UsableMandateOut(
        organization_id=organization_id,
        currency=currency.upper(),
        amount_minor=amount_minor,
        usable=mandate is not None,
        mandate_id=mandate.id if mandate is not None else None,
    )
