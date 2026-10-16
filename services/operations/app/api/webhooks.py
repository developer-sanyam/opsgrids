"""The public webhook surface for connected channels -- signature-guarded, never token-guarded.

`POST /api/v1/webhooks/operations/{provider}?connection=<channel id>`. A provider cannot hold one
of our tokens: the body is authenticated by the signature the provider puts on it, verified here
against **that connection's own** sealed credential (`app.channels.webhooks`). Everything that can
be wrong with a delivery before it is trusted -- an unknown provider, connection or signature --
answers the same 400 and records nothing. An authentic delivery is recorded once and answered 200,
a redelivery included.

Routed by the gateway tables with a body cap. Public per provider, by exact path and POST only
(`WEBHOOK_PUBLIC_PATHS`): so far `/webhooks/operations/parcel-track`, Shiprocket's tracking
push (the last segment is a slug, `app.channels.registry.WEBHOOK_SLUGS`, because Shiprocket
forbids its own name in a webhook URL). A provider with no verifier or no public path answers 400.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.channels import selectors as channel_selectors
from app.channels import services as channel_services
from app.channels.registry import TRACKING_MAPPERS, VERIFIERS, WEBHOOK_SLUGS
from app.channels.webhooks import (
    IntegrationWebhookProblem,
    read_capped_body,
    record_event,
    webhook_channel,
)
from app.dispatch import services as dispatch_services
from app.schemas import IntegrationWebhookAck
from platform_core.db import get_outbox_sessionmaker, get_session, set_tenant
from platform_core.errors import UpstreamUnavailableProblem, ValidationProblem
from platform_core.logging import get_logger

log = get_logger("operations.integration_webhooks")

router = APIRouter(prefix="/webhooks/operations", tags=["webhooks"])

RawSessionDep = Annotated[AsyncSession, Depends(get_session)]

#: "The database is not there", and only that: the redelivery will find it back, so 503. Anything
#: else is a defect and stays a 500.
TRANSIENT_DATABASE_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    InterfaceError,
    PoolTimeoutError,
    OSError,
)


def _reject(reason: str, **context: object) -> IntegrationWebhookProblem:
    log.warning("integration_webhook.rejected", reason=reason, **context)
    return IntegrationWebhookProblem("could not verify the webhook")


@router.post(
    "/{slug}",
    response_model=IntegrationWebhookAck,
    summary="A connected channel's webhook -- per-connection signature, idempotent inbox",
)
async def integration_webhook(
    slug: str,
    request: Request,
    session: RawSessionDep,
    connection: Annotated[str, Query(min_length=1, max_length=64)],
) -> IntegrationWebhookAck:
    body = await read_capped_body(request)
    provider = WEBHOOK_SLUGS.get(slug, slug)
    verifier = VERIFIERS.get(provider)
    if verifier is None:
        raise _reject("no_verifier", provider=provider[:30])
    try:
        channel_id = uuid.UUID(connection)
    except ValueError:
        raise _reject("bad_connection_id", provider=provider) from None
    headers = {name.lower(): value for name, value in request.headers.items()}

    try:
        async with get_outbox_sessionmaker()() as bypass, bypass.begin():
            found = await webhook_channel(bypass, channel_id)
        if found is None or found[1] != provider or found[2] != "connected":
            raise _reject("unknown_or_disconnected", provider=provider)
        organization_id = found[0]
        async with session.begin():
            await set_tenant(session, organization_id)
            channel = await channel_selectors.get_channel(session, organization_id, channel_id)
            if channel is None:
                raise _reject("channel_gone", provider=provider)
            try:
                secret = await channel_services.open_channel_credential(channel)
            except ValidationProblem:
                raise _reject("no_credential", provider=provider) from None
            recorded = await record_event(
                session,
                channel=channel,
                verifier=verifier,
                headers=headers,
                body=body,
                secret=secret,
            )
            mapper = TRACKING_MAPPERS.get(provider)
            update = mapper(recorded.payload) if mapper else None
            if update and not recorded.duplicate and recorded.outcome == "received":
                awb, new_status, occurred_at = update
                await dispatch_services.apply_carrier_tracking(
                    session,
                    organization_id,
                    channel_id,
                    awb=awb,
                    status=new_status,
                    reason=f"carrier report {occurred_at.isoformat()}",
                )
    except TRANSIENT_DATABASE_ERRORS as exc:
        log.error("integration_webhook.unavailable", error=type(exc).__name__)
        raise UpstreamUnavailableProblem("temporarily unable to record the webhook") from exc

    log.info(
        "integration_webhook.recorded",
        organization_id=str(organization_id),
        channel_id=str(channel_id),
        provider=provider,
        outcome=recorded.outcome,
        duplicate=recorded.duplicate,
    )
    return IntegrationWebhookAck(
        event_id=recorded.event_id, outcome=recorded.outcome, duplicate=recorded.duplicate
    )
