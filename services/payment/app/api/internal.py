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

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services
from app.deps import get_provider
from app.providers.base import PaymentProvider
from app.schemas import ChargeIn, ChargeOut
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
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
