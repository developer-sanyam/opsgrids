"""Meta's inbound webhooks — the only unauthenticated surface this service exposes.

It is unauthenticated by necessity: Meta cannot hold one of our tokens. So the signature is the
whole of the trust boundary, and everything here is arranged around that.

Four rules, each with a failure it prevents:

1. **The raw bytes are read before anything parses them.** The signature covers the body Meta
   sent; parsing to JSON and re-serialising changes whitespace and key order, so the digest
   would never match and every genuine webhook would be rejected. There is a test asserting
   exactly that trap.

2. **An unsigned or wrongly-signed body gets 401 and nothing else happens.** No parsing, no
   lookup, no row. A forged delivery receipt is a lie about a customer; a forged inbound message
   would be words attributed to one.

3. **A 200 is returned for anything genuine, including what we cannot use.** Meta retries what
   it does not get a 200 for, so failing on an unfamiliar payload is a retry loop, not
   robustness. Unusable bodies are acknowledged and logged.

4. **The tenant comes from the payload, not the caller.** One URL serves every organization, so
   `metadata.phone_number_id` is looked up on the directory reader — the one read that happens
   before a tenant is known — and everything after it runs tenant-bound.

Mounted outside `/api/v1` and excluded from the OpenAPI document: this is Meta's endpoint, not
public API, and it must not appear as though anyone else should call it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services, webhooks
from app.config import settings
from app.models import ProcessedEvent
from platform_core.db import get_directory_session, session_scope
from platform_core.errors import UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("notification.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)

DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]


@router.get("/whatsapp", response_class=Response)
async def verify(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Meta's subscription handshake: echo `hub.challenge` if the token matches.

    403 on a mismatch, which is what Meta expects and what stops anyone else pointing their
    app at this endpoint. A service with no token configured refuses everything rather than
    accepting an empty one and handing over the endpoint.
    """
    cfg = settings()
    expected = cfg.whatsapp_verify_token.get_secret_value() if cfg.whatsapp_verify_token else ""
    try:
        echoed = webhooks.verification_challenge(mode, token, challenge, expected_token=expected)
    except webhooks.WebhookError as exc:
        log.warning("webhook.verification_refused", reason=str(exc))
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    return Response(content=echoed, media_type="text/plain")


@router.post("/whatsapp", response_class=Response)
async def receive(request: Request, directory: DirectorySessionDep) -> Response:
    """Accept one webhook body: verify it, attribute it, fold what it carries.

    Returns 200 for anything genuine. The only non-200 is 401 for a body that is not signed by
    the App Secret, because that is the one case where acknowledging would be wrong.
    """
    cfg = settings()
    if not cfg.whatsapp_app_secret:
        # Refusing rather than accepting unverified bodies. A misconfigured deployment that
        # trusted everything would be worse than one that trusts nothing.
        log.error("webhook.no_app_secret_configured")
        raise UnauthorizedProblem("Webhook signature verification is not configured")

    # Rule 1: the exact bytes, before anything parses them.
    raw = await request.body()
    if not webhooks.verify_signature(
        raw,
        request.headers.get(webhooks.SIGNATURE_HEADER),
        cfg.whatsapp_app_secret.get_secret_value(),
    ):
        # Deliberately says nothing about which half was wrong.
        log.warning("webhook.signature_refused", bytes=len(raw))
        raise UnauthorizedProblem("Invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        log.warning("webhook.undecodable")
        return Response(status_code=status.HTTP_200_OK)

    number_id = webhooks.phone_number_id(payload)
    if number_id is None:
        log.info("webhook.no_phone_number_id")
        return Response(status_code=status.HTTP_200_OK)

    account = await selectors.account_for_phone_number(directory, number_id)
    if account is None:
        # A number nobody has connected. Genuine and signed, but not ours to act on — a WABA
        # removed from the platform, or one subscribed to this app by mistake.
        log.warning("webhook.unknown_phone_number_id", phone_number_id=number_id)
        return Response(status_code=status.HTTP_200_OK)

    organization_id = account.organization_id
    updates = webhooks.parse_statuses(payload)

    for update in updates:
        # One transaction per update, each tenant-bound and idempotent. Per update rather than
        # per body so one unknown message id cannot roll back the receipts beside it — Meta
        # batches them and will not send the others again.
        async with session_scope(organization_id=organization_id) as session:
            already = await session.get(ProcessedEvent, update.dedupe_id)
            if already is not None:
                log.info(
                    "webhook.redelivered",
                    provider_message_id=update.provider_message_id,
                    status=update.status,
                )
                continue

            await services.fold_status(
                session,
                organization_id=organization_id,
                provider_message_id=update.provider_message_id,
                status=update.status,
                occurred_at=update.occurred_at,
                error_code=update.error_code,
                error_detail=update.error_detail,
            )
            # Marked processed even when the fold applied nothing. The event *has* been handled:
            # re-running it would reach the same conclusion, and leaving it unmarked would make
            # every redelivery repeat the work.
            session.add(
                ProcessedEvent(
                    event_id=update.dedupe_id,
                    event_type=f"whatsapp.status.{update.status}",
                )
            )

    log.info("webhook.accepted", phone_number_id=number_id, statuses=len(updates))
    return Response(status_code=status.HTTP_200_OK)
