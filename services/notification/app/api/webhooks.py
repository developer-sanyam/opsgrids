"""Meta's inbound webhooks — the only unauthenticated surface this service exposes.

It is unauthenticated by necessity: Meta cannot hold one of our tokens. So the signature is the
whole of the trust boundary, and everything here is arranged around that.

Five rules, each with a failure it prevents:

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

4. **The tenant comes from each `change`, never from the body as a whole.** One URL serves every
   organization, and Meta batches several WABAs' changes into one `POST` behind a single App
   Secret — `webhooks.group_statuses_by_phone`/`group_inbound_by_phone` resolve `metadata.
   phone_number_id` per change and this route asks the directory reader once per **distinct**
   number, so organization A's webhook can never be folded against organization B's rows just
   because Meta happened to batch them together.

5. **One item's failure parks that item — but only when nothing would change on redelivery.**
   Each status or message folds inside its own `try`/`except`, and `services.
   is_permanent_fold_error` is what decides what happens next. A **permanent** failure (a value
   that will never fit a column, a duplicate this exact item already won elsewhere) is parked in
   `services.record_dead_letter` and the request still returns 200 for it — a single malformed
   item must not turn into Meta retrying the *whole* body forever over one piece of it that can
   never succeed. A **transient** failure (a dropped connection, a deadlock, the database
   restarting) is left to raise: the whole request then answers with a 5xx instead of a 200, so
   Meta redelivers the body and this item gets a genuine second attempt. Every other item in the
   batch is still folded first — `processed_events` is what makes that safe to redeliver into:
   whatever already folded dedupes on the way back, and only the one that actually failed does
   any work the second time.

Mounted outside `/api/v1` and excluded from the OpenAPI document: this is Meta's endpoint, not
public API, and it must not appear as though anyone else should call it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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

    **Unbounded batch size, deliberately not capped here.** Meta's own limits on how much one
    `POST` can carry are the real bound; this route folds every item it is handed, one at a time,
    each in its own short transaction, so an unusually large body costs time (and, per item, a
    directory lookup the first time a new number is seen) rather than memory held open across the
    whole request. If Meta's batching ever produces a body large enough for that to matter in
    practice, the fix is a per-request item cap answered with a fast 200 and a warning log — not
    yet added, because nothing has been observed to need it.
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

    # Rule 4: resolved **per number**, never once for the whole body. Meta can and does batch
    # several WABAs into one POST; a single `organization_id` picked from the first change would
    # fold every other change in the body against it.
    status_groups = webhooks.group_statuses_by_phone(payload)
    inbound_groups = webhooks.group_inbound_by_phone(payload)
    waba_ids_by_phone = webhooks.entry_ids_by_phone(payload)
    phone_ids = set(status_groups) | set(inbound_groups)

    if not phone_ids:
        log.info("webhook.no_phone_number_id")
        return Response(status_code=status.HTTP_200_OK)

    total_statuses = 0
    total_messages = 0
    # Rule 5: a transient failure anywhere in the batch makes the *whole request* answer 5xx, so
    # Meta redelivers the body — but every other item is still folded first. `processed_events`
    # is what makes that safe: whatever already folded dedupes on the way back, and only the item
    # that actually failed does any work the second time.
    had_transient_failure = False

    for phone_id in phone_ids:
        account = await selectors.account_for_phone_number(directory, phone_id)
        if account is None:
            # A number nobody has connected. Genuine and signed, but not ours to act on — a WABA
            # removed from the platform, or one subscribed to this app by mistake. The *other*
            # numbers in this same body, if any, are still folded below — one unknown number must
            # not discard changes that belong to a number we do recognise.
            log.warning("webhook.unknown_phone_number_id", phone_number_id=phone_id)
            continue

        # The WABA this batch claims sent the change must be the WABA this number is actually
        # registered under. A phone number moved to a different WABA without this service's own
        # record being updated (a stale `ProviderAccount.waba_id`) is exactly the shape a routing
        # bug would produce, and the signature alone does not prove which WABA a *number* belongs
        # to — only that the platform's own App Secret signed the body.
        seen_waba_ids = waba_ids_by_phone.get(phone_id, set())
        if seen_waba_ids and account.waba_id not in seen_waba_ids:
            log.warning("webhook.waba_mismatch", phone_number_id=phone_id)
            continue

        organization_id = account.organization_id
        updates = status_groups.get(phone_id, [])
        inbound_messages = inbound_groups.get(phone_id, [])
        total_statuses += len(updates)
        total_messages += len(inbound_messages)

        for update in updates:
            # One transaction per update, each tenant-bound and idempotent. Per update rather
            # than per body so one unknown message id cannot roll back the receipts beside it —
            # Meta batches them and will not send the others again.
            try:
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
                    # Marked processed even when the fold applied nothing. The event *has* been
                    # handled: re-running it would reach the same conclusion, and leaving it
                    # unmarked would make every redelivery repeat the work.
                    session.add(
                        ProcessedEvent(
                            event_id=update.dedupe_id,
                            event_type=f"whatsapp.status.{update.status}",
                        )
                    )
            except Exception as exc:
                if not services.is_permanent_fold_error(exc):
                    log.error(
                        "webhook.status_fold_transient_error",
                        provider_message_id=update.provider_message_id,
                        error_type=type(exc).__name__,
                    )
                    had_transient_failure = True
                    continue
                # No PII in the log line — a provider message id names nothing about the
                # customer it concerns.
                log.error(
                    "webhook.status_fold_failed",
                    provider_message_id=update.provider_message_id,
                    error_type=type(exc).__name__,
                )
                if not await _dead_letter(
                    organization_id=organization_id,
                    kind="status",
                    provider_message_id=update.provider_message_id,
                    occurred_at=update.occurred_at,
                    error=exc,
                    raw=update.raw or {},
                ):
                    had_transient_failure = True

        for inbound in inbound_messages:
            # Same shape as the status loop: one transaction per message, idempotent on its own
            # derived id, and marked processed whether or not `fold_inbound_message` found a
            # sender it could canonicalise — see that function's docstring for why a bad `from`
            # is not something retrying will ever fix.
            try:
                async with session_scope(organization_id=organization_id) as session:
                    already = await session.get(ProcessedEvent, inbound.dedupe_id)
                    if already is not None:
                        log.info(
                            "webhook.redelivered",
                            provider_message_id=inbound.provider_message_id,
                        )
                        continue

                    await services.fold_inbound_message(
                        session, organization_id=organization_id, inbound=inbound
                    )
                    session.add(
                        ProcessedEvent(
                            event_id=inbound.dedupe_id,
                            event_type="whatsapp.message.inbound",
                        )
                    )
            except Exception as exc:
                if not services.is_permanent_fold_error(exc):
                    log.error(
                        "webhook.inbound_fold_transient_error",
                        provider_message_id=inbound.provider_message_id,
                        error_type=type(exc).__name__,
                    )
                    had_transient_failure = True
                    continue
                log.error(
                    "webhook.inbound_fold_failed",
                    provider_message_id=inbound.provider_message_id,
                    error_type=type(exc).__name__,
                )
                if not await _dead_letter(
                    organization_id=organization_id,
                    kind="inbound",
                    provider_message_id=inbound.provider_message_id,
                    occurred_at=inbound.occurred_at,
                    error=exc,
                    raw=inbound.raw,
                ):
                    had_transient_failure = True

    log.info(
        "webhook.accepted",
        phone_numbers=len(phone_ids),
        statuses=total_statuses,
        messages=total_messages,
        had_transient_failure=had_transient_failure,
    )
    if had_transient_failure:
        # Everything foldable in this body has been folded; what remains is exactly the item(s)
        # that hit a transient error. Answering 5xx tells Meta to redeliver the *whole* body —
        # safe because `processed_events` dedupes everything that already succeeded, so the
        # retry only repeats the work the failed item still owes.
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_200_OK)


async def _dead_letter(
    *,
    organization_id: uuid.UUID,
    kind: str,
    provider_message_id: str,
    occurred_at: datetime,
    error: BaseException,
    raw: dict[str, Any],
) -> bool:
    """Record one **permanent** failure in its own transaction — the fold's own transaction has
    already rolled back by the time this runs, so a fresh `session_scope` is deliberate, not a
    leftover. Returns whether the write itself succeeded.

    A failure recording the failure is **not** swallowed the way it once was: parking a permanent
    error is only safe to acknowledge with a 200 if the parking itself actually happened, or the
    item is lost twice over — once to whatever made it permanent, and again because nobody could
    ever find out it happened. The caller checks this function's return value and, on `False`,
    treats the whole request the same as a transient failure so Meta redelivers.
    """
    try:
        async with session_scope(organization_id=organization_id) as session:
            await services.record_dead_letter(
                session,
                organization_id=organization_id,
                kind=kind,
                provider_message_id=provider_message_id,
                occurred_at=occurred_at,
                error=error,
                raw=raw,
            )
    except Exception as exc:
        log.error(
            "webhook.dead_letter_write_failed",
            provider_message_id=provider_message_id,
            error_type=type(exc).__name__,
        )
        return False
    return True
