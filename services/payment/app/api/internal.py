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
    """Raise a charge, idempotently on the platform key.

    Three acts, at most two transactions, and the provider call spans neither. **Claim**: bind the
    transaction to the payload's organization and stage the charge row; commit. **Call**: ask the
    provider, with no session and no transaction open — a slow or hanging provider then holds
    nothing from this service's pool. **Record**: bind a fresh transaction to the same
    organization and finalize what the provider said. A process that dies between the call and the
    record leaves the charge `created` with no `provider_payment_id`, which is exactly what
    `app/cron.py`'s stuck-charge sweep resumes; `call_provider_charge` is safe to run again because
    it looks the payment up (by the Order whose receipt is the platform's key) before it creates
    one. A retry of the same key while the first call is still in flight is answered with the
    charge as it stands — `stage_charge` hands the right to call to one caller at a time.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        charge, needs_call = await services.stage_charge(session, provider, payload)

    if needs_call:
        # On a worker thread, bounded: a provider that does not answer in time leaves the charge
        # `created` under its lease, and the stuck-charge sweep resumes it once the lease lapses.
        result = await services.call_provider_charge_off_loop(provider, charge)
        if result is not None:
            async with session.begin():
                await set_tenant(session, payload.organization_id)
                if not settings.is_production:
                    await assert_tenant(session, payload.organization_id)
                charge = await services.finalize_charge(session, charge.id, result, request=request)

    result_out = ChargeOut.model_validate(charge)
    log.info(
        "payment.charge.created",
        charge_id=str(result_out.id),
        organization_id=str(payload.organization_id),
        state=result_out.state,
    )
    return result_out
