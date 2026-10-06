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

Mounted under `/api/v1` so its path is `/api/v1/webhooks/razorpay`, which is what the gateway
forwards, body and signature header untouched. **Deliberately not public on the app gateway today**
(owner decision, ADR-0032): platform billing learns outcomes by polling the provider
(`app/checkout/cron.py`), and a public path is a separate, owner-gated change that also touches the
gateway's public-path lists, both route tables and their parity tests. Everything on this side — the
signature check, the idempotent inbox, the payload redaction, the body cap — is built and tested so
that change is only a route-table edit.

**Cashfree** has its own route (`/webhooks/cashfree`) with the same ack rules. It signs a timestamp
header plus the body (`x-webhook-timestamp`, `x-webhook-signature`), carries its delivery id in
`x-idempotency-header` (read under both documented spellings; the real one is for the smoke tool to
confirm), has no secret of its own — the API Secret Key signs — and answers **404 unless Cashfree is
enabled** here (it is the default provider, or `CASHFREE_ENV` is `sandbox` / `production`), so a
public route could never run on an unconfigured box.
"""

from __future__ import annotations

from typing import Annotated

from asyncpg import exceptions as pg_exc  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services
from app.config import settings
from app.deps import cashfree_provider, razorpay_provider
from app.providers.base import PaymentProvider
from app.schemas import WebhookAck
from platform_core.config import ServiceSettings
from platform_core.db import get_session
from platform_core.errors import NotFoundProblem, PayloadTooLargeProblem, UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("payment.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

#: What "the database is not there" looks like from this route, and only that. SQLAlchemy wraps
#: most driver failures as `OperationalError` / `InterfaceError` / a pool `TimeoutError`, but a
#: refused or unreachable connection surfaces as a raw `OSError` (`ConnectionRefusedError`, a
#: DNS `gaierror`, a `TimeoutError`) and the server's own "not now" answers as asyncpg errors —
#: `PostgresConnectionError` (a connection lost or never made), `CannotConnectNowError` (starting up
#: or shutting down), `TooManyConnectionsError`. Every one is transient: the redelivery finds the
#: database back. **Nothing else belongs here** — a `DataError`, a `RuntimeError`, an unrelated
#: `IntegrityError` is a defect, and a defect stays a 500 (or the platform's 409) so it is seen and
#: never dressed up as an outage. The `try` below wraps only the transaction, whose sole I/O is the
#: database, which is what makes a bare `OSError` (a `TimeoutError` included — it subclasses
#: `OSError`) safe to claim here without also catching a provider call gone wrong: the transaction
#: never makes one, and `tests/infra/test_io_outside_transaction.py` fails the build the day it
#: ever does.
TRANSIENT_DATABASE_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    InterfaceError,
    PoolTimeoutError,
    OSError,
    pg_exc.PostgresConnectionError,
    pg_exc.CannotConnectNowError,
    pg_exc.TooManyConnectionsError,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
#: Each route is bound to **its own** adapter, never to whichever provider is the default now: a
#: Razorpay delivery is verified with Razorpay's secret and a Cashfree one with Cashfree's, whatever
#: `PAYMENT_PROVIDER` says (ADR-0032).
ProviderDep = Annotated[PaymentProvider, Depends(razorpay_provider)]
CashfreeProviderDep = Annotated[PaymentProvider, Depends(cashfree_provider)]

#: The largest platform webhook body this service will buffer, enforced **before** the signature is
#: checked. Provider bodies are a few kilobytes; the merchant route holds the same ceiling.
MAX_WEBHOOK_BODY_BYTES = 256 * 1024

#: Razorpay's signature header. Case-insensitive at the HTTP layer; read via `.get`.
SIGNATURE_HEADER = "X-Razorpay-Signature"
#: Razorpay's per-delivery event id. It is **only** a header: the body Razorpay sends has no
#: top-level `id`, so this is the one place the id the inbox deduplicates on can come from. It is
#: not covered by the signature (the HMAC is over the body alone), and `services.ingest_webhook`
#: treats it accordingly — a delivery id, validated, with a digest of the body's own identifying
#: fields as the fallback when it is absent or unusable.
EVENT_ID_HEADER = "X-Razorpay-Event-Id"

#: Cashfree's signature and delivery-id headers. The id is documented as `x-idempotency-header` (a
#: payload hash, stable across retries, on webhook versions 2025-01-01 and later) and appears as
#: `x-idempotency-key` in some samples: both are read, and — being unsigned — treated as a delivery
#: id and nothing more, exactly like Razorpay's.
CASHFREE_SIGNATURE_HEADER = "x-webhook-signature"
CASHFREE_EVENT_ID_HEADERS = ("x-idempotency-header", "x-idempotency-key")


async def read_capped_body(request: Request, limit: int) -> bytes:
    """The raw body, refused (413) above `limit` bytes — before any signature work, and never by
    buffering it first: from the declared length when there is one, and otherwise (a chunked body
    carries none) by counting the stream as it arrives and stopping at the first byte over."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeProblem("webhook body too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeProblem("webhook body too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _capped_body(request: Request) -> bytes:
    return await read_capped_body(request, MAX_WEBHOOK_BODY_BYTES)


def cashfree_enabled() -> bool:
    """Whether this deployment can have a Cashfree webhook at all: Cashfree is the provider new
    charges go to, or its environment is switched to `sandbox` / `production`. False on the stub
    with another default — the route is then a 404, whatever a caller sends."""
    cfg = settings()
    return bool(
        getattr(cfg, "payment_provider", "razorpay") == "cashfree"
        or getattr(cfg, "cashfree_env", "stub") in ("sandbox", "production")
    )


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
    body = await _capped_body(request)
    signature = request.headers.get(SIGNATURE_HEADER)
    event_id = request.headers.get(EVENT_ID_HEADER)
    app_settings: ServiceSettings = request.app.state.settings

    try:
        async with session.begin():
            ack = await services.ingest_webhook(
                session,
                provider,
                signature=signature,
                body=body,
                is_production=app_settings.is_production,
                event_id=event_id,
                request=request,
            )
    except TRANSIENT_DATABASE_ERRORS as exc:
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


@router.post(
    "/cashfree",
    response_model=WebhookAck,
    summary="Cashfree webhook — signature-verified, idempotent, reconciling (404 unless enabled)",
)
async def cashfree_webhook(
    request: Request, session: SessionDep, provider: CashfreeProviderDep
) -> WebhookAck:
    """Ingest one Cashfree PG webhook. The body is read raw — the signature is
    `base64(HMAC-SHA256(secret, timestamp + body))` over exactly these bytes and the
    `x-webhook-timestamp` header — and capped before it is touched. A missing secret, header or
    match is a 400 with nothing recorded (fail closed)."""
    if not cashfree_enabled():
        raise NotFoundProblem("Not found")
    body = await _capped_body(request)
    headers = request.headers
    event_id = next((headers[name] for name in CASHFREE_EVENT_ID_HEADERS if name in headers), None)
    app_settings: ServiceSettings = request.app.state.settings
    try:
        async with session.begin():
            ack = await services.ingest_webhook(
                session,
                provider,
                signature=headers.get(CASHFREE_SIGNATURE_HEADER),
                body=body,
                is_production=app_settings.is_production,
                event_id=event_id,
                request=request,
                headers={k.lower(): v for k, v in headers.items()},
            )
    except TRANSIENT_DATABASE_ERRORS as exc:
        log.error("cashfree.webhook.unavailable", error=type(exc).__name__)
        raise UpstreamUnavailableProblem("temporarily unable to record the webhook") from exc
    log.info(
        "cashfree.webhook",
        provider_event_id=ack.provider_event_id,
        duplicate=ack.duplicate,
        reconciled=ack.reconciled,
        ignored_reason=ack.ignored_reason,
    )
    return ack
