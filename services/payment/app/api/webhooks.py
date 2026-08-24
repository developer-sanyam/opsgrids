"""The provider webhook surface — signature-guarded, never token-guarded.

Razorpay calls this. It carries **no staff token and no service token**: a provider webhook is
authenticated by the HMAC signature the provider puts on the body, verified in `services` against
the shared webhook secret. That is why the admin-gateway routes `/api/v1/webhooks/*` as a
*public* path (no bearer required) — the authentication lives here, at the signature, not at the
gateway. A missing or wrong signature is a 400 and nothing is recorded.

Mounted under `/api/v1` so its public path is `/api/v1/webhooks/razorpay`, which is exactly what
the admin-gateway forwards, body and signature header untouched.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services
from app.deps import get_provider
from app.providers.base import PaymentProvider
from app.schemas import WebhookAck
from platform_core.config import ServiceSettings
from platform_core.db import get_session
from platform_core.logging import get_logger

log = get_logger("payment.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[PaymentProvider, Depends(get_provider)]

#: Razorpay's signature header. Case-insensitive at the HTTP layer; read via `.get`.
SIGNATURE_HEADER = "X-Razorpay-Signature"
#: Razorpay's per-delivery event id header (also echoed in the body `id`).
EVENT_ID_HEADER = "X-Razorpay-Event-Id"


@router.post(
    "/razorpay",
    response_model=WebhookAck,
    summary="Razorpay webhook — signature-verified, idempotent, reconciling",
)
async def razorpay_webhook(
    request: Request, session: SessionDep, provider: ProviderDep
) -> WebhookAck:
    """Ingest one Razorpay webhook. The body is read raw — the signature is over exactly these
    bytes, so it must not be re-serialized before verification."""
    body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)
    settings: ServiceSettings = request.app.state.settings

    async with session.begin():
        ack = await services.ingest_webhook(
            session,
            provider,
            signature=signature,
            body=body,
            is_production=settings.is_production,
            request=request,
        )
    log.info(
        "razorpay.webhook",
        provider_event_id=ack.provider_event_id,
        duplicate=ack.duplicate,
        reconciled=ack.reconciled,
    )
    return ack
