"""The provider webhook surface — signature-guarded, never token-guarded.

Razorpay calls this. It carries **no staff token and no service token**: a provider webhook is
authenticated by the HMAC signature the provider puts on the body, verified in `services` against
the shared webhook secret. That is why the admin-gateway routes `/api/v1/webhooks/*` as a
*public* path (no bearer required) — the authentication lives here, at the signature, not at the
gateway. A missing or wrong signature is a 400 and nothing is recorded.

**What is and is not a non-2xx.** Razorpay redelivers any non-2xx for days, so a status code is a
promise that a retry can help. Three things earn one: a bad signature (400 — not Razorpay's), a
webhook that cannot even be identified (400 — no usable event id, or not a JSON object), and a
*transient* failure to record it (503 — the database was unreachable, and the retry will find it
back). Everything else that is wrong with an authentic webhook is permanent — a charge that is not
ours, an outcome the state machine forbids, another amount or currency — and is recorded, audited
and answered **200** (`services.ingest_webhook`), because a retry could not change it.

Mounted under `/api/v1` so its public path is `/api/v1/webhooks/razorpay`, which is exactly what
the admin-gateway forwards, body and signature header untouched.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services
from app.deps import get_provider
from app.providers.base import PaymentProvider
from app.schemas import WebhookAck
from platform_core.config import ServiceSettings
from platform_core.db import get_session
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("payment.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[PaymentProvider, Depends(get_provider)]

#: Razorpay's signature header. Case-insensitive at the HTTP layer; read via `.get`.
SIGNATURE_HEADER = "X-Razorpay-Signature"
#: Razorpay's per-delivery event id. It is **only** a header: the body Razorpay sends has no
#: top-level `id`, so this is the one place the id the inbox deduplicates on can come from. It is
#: not covered by the signature (the HMAC is over the body alone), and `services.ingest_webhook`
#: treats it accordingly — a delivery id, validated, with a digest of the body's own identifying
#: fields as the fallback when it is absent or unusable.
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
    event_id = request.headers.get(EVENT_ID_HEADER)
    settings: ServiceSettings = request.app.state.settings

    try:
        async with session.begin():
            ack = await services.ingest_webhook(
                session,
                provider,
                signature=signature,
                body=body,
                is_production=settings.is_production,
                event_id=event_id,
                request=request,
            )
    except (OperationalError, InterfaceError, PoolTimeoutError) as exc:
        # The database was unreachable, or the pool exhausted: transient. The transaction has
        # rolled back — nothing was recorded, the event id is not burned — so the provider's
        # redelivery starts clean. Answered 503 rather than a bare 500 so the intent is stated; the
        # exception's text (a DSN, a query) is not echoed.
        log.error("razorpay.webhook.unavailable", error=type(exc).__name__)
        raise UpstreamUnavailableProblem("temporarily unable to record the webhook") from exc
    log.info(
        "razorpay.webhook",
        provider_event_id=ack.provider_event_id,
        duplicate=ack.duplicate,
        reconciled=ack.reconciled,
        ignored_reason=ack.ignored_reason,
    )
    return ack
