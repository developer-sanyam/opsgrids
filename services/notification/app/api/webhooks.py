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
   Each status or message folds inside its own `try`/`except`. A genuine duplicate on
   `inbound_messages` (`services.is_inbound_dedupe_conflict`) is checked first and treated as
   **success** — the message is already durably stored by whichever transaction won the race, so
   this one is marked processed (`_mark_processed`) and nothing is parked. Otherwise, `services.
   is_permanent_fold_error` decides: a **permanent** failure (a value that will never fit a
   column, a shape that will never parse) is parked in `services.record_dead_letter` and the
   request still returns 200 for it — a single malformed item must not turn into Meta retrying the
   *whole* body forever over one piece of it that can never succeed. A **transient** failure (a
   dropped connection, a deadlock, the database restarting) is left to raise: the whole request
   then answers with a 5xx instead of a 200, so Meta redelivers the body and this item gets a
   genuine second attempt — unless it has already been retried past `webhooks.
   TRANSIENT_RETRY_CAP`, in which case it is parked the same way a permanent failure is, rather
   than asking for a 503 forever over a connection problem `TRANSIENT_RETRY_CAP` has not cleared.
   Every other item in the batch is still folded first — `processed_events` is what makes that
   safe to redeliver into: whatever already folded dedupes on the way back, and only the one
   that actually failed does any work the second time.

Mounted at `/api/v1/webhooks/whatsapp` (the path app-gateway forwards, unchanged) and at the root
`/webhooks/whatsapp` (for a tunnel pointed straight at this service), and excluded from the
OpenAPI document: this is Meta's endpoint, not public API, and it must not appear as though anyone
else should call it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services, w2_webhooks, webhooks
from app.config import meta_app_secret, settings
from app.models import ProcessedEvent
from platform_core.db import get_directory_session, session_scope
from platform_core.errors import PayloadTooLargeProblem, UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("notification.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)

DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]

#: The most one webhook body may carry: Meta documents payloads up to 3 MB, and this is the same
#: figure as `max_body_bytes` on the `/api/v1/webhooks/whatsapp` entry in both gateway route
#: tables (`tests/infra/test_route_table_parity.py` holds the three together). The gateway is the
#: first line; this is the second, for what reaches the service without it (a tunnel in
#: development, a sibling container). It matters *here* because the body is read before the
#: signature can be checked — an unauthenticated caller must not be able to make this process
#: buffer an unbounded one just to be told 401.
MAX_WEBHOOK_BODY_BYTES = 3 * 1024 * 1024


async def _read_capped_body(request: Request, limit: int) -> bytes:
    """The request's raw bytes, refusing (413) anything past `limit` before it is all in memory.

    Checked twice, because `Content-Length` is a claim and not a fact: against the declared
    length, which refuses an oversized body without reading a byte of it, and against the bytes
    actually read, which catches a header that lies and a chunked request that sends none. The
    counter is the enforcement; the header is only an early exit.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeProblem(f"Request body exceeds the {limit} byte limit")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            # Stop reading here: draining the rest would buffer exactly what was refused.
            raise PayloadTooLargeProblem(f"Request body exceeds the {limit} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


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

    Returns 200 for anything genuine. The only non-200s are 401 for a body that is not signed by
    the App Secret, because that is the one case where acknowledging would be wrong, and 413 for a
    body past `MAX_WEBHOOK_BODY_BYTES` — refused while it is being read, so it is never buffered
    whole, and before the signature check, which needs the whole of it.

    **Item count is deliberately not capped here.** Within that byte ceiling, Meta's own limits
    are the real bound; this route folds every item it is handed, one at a time, each in its own
    short transaction, so an unusually large body costs time (and, per item, a directory lookup
    the first time a new number is seen) rather than memory held open across the whole request. If
    Meta's batching ever produces a body large enough for that to matter in practice, the fix is a
    per-request item cap answered with a fast 200 and a warning log — not yet added, because
    nothing has been observed to need it.
    """
    cfg = settings()
    # The same helper Embedded Signup uses (`META_APP_SECRET`, else the older
    # `WHATSAPP_APP_SECRET`), so the secret that exchanges a signup code is the one that verifies
    # the webhooks — setting either variable works, and the two can never disagree about which is
    # "the" app secret.
    app_secret = meta_app_secret(cfg)
    if not app_secret:
        # Refusing rather than accepting unverified bodies. A misconfigured deployment that
        # trusted everything would be worse than one that trusts nothing.
        log.error("webhook.no_app_secret_configured")
        raise UnauthorizedProblem("Webhook signature verification is not configured")

    # Rule 1: the exact bytes, before anything parses them — capped, since nothing about the caller
    # has been established yet.
    try:
        raw = await _read_capped_body(request, MAX_WEBHOOK_BODY_BYTES)
    except PayloadTooLargeProblem:
        log.warning(
            "webhook.body_too_large",
            limit=MAX_WEBHOOK_BODY_BYTES,
            declared=request.headers.get("content-length"),
        )
        raise
    if not webhooks.verify_signature(
        raw,
        request.headers.get(webhooks.SIGNATURE_HEADER),
        app_secret,
    ):
        # Deliberately says nothing about which half was wrong.
        log.warning("webhook.signature_refused", bytes=len(raw))
        raise UnauthorizedProblem("Invalid signature")

    try:
        # From the bytes already read (and verified): the stream is consumed, so `request.json()`
        # would have nothing left to read.
        payload: dict[str, Any] = json.loads(raw)
    except Exception:
        log.warning("webhook.undecodable")
        return Response(status_code=status.HTTP_200_OK)

    # Account-level changes (template status and quality, number quality) name a WABA rather than a
    # number, so they are routed by `w2_webhooks` from `entry.id`, before the per-number folds and
    # independent of whether the body carries any number at all.
    template_transient = await w2_webhooks.handle_changes(directory, payload)

    # Rule 4: resolved **per number**, never once for the whole body. Meta can and does batch
    # several WABAs into one POST; a single `organization_id` picked from the first change would
    # fold every other change in the body against it.
    status_groups = webhooks.group_statuses_by_phone(payload)
    inbound_groups = webhooks.group_inbound_by_phone(payload)
    waba_ids_by_phone = webhooks.entry_ids_by_phone(payload)
    phone_ids = set(status_groups) | set(inbound_groups)

    if not phone_ids:
        log.info("webhook.no_phone_number_id")
        if template_transient:
            return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(status_code=status.HTTP_200_OK)

    total_statuses = 0
    total_messages = 0
    # Rule 5: a transient failure anywhere in the batch makes the *whole request* answer 5xx, so
    # Meta redelivers the body — but every other item is still folded first. `processed_events`
    # is what makes that safe: whatever already folded dedupes on the way back, and only the item
    # that actually failed does any work the second time.
    had_transient_failure = template_transient

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
                    if (
                        update.status == webhooks.STATUS_FAILED
                        and update.error_code == webhooks.ERROR_RECIPIENT_STOPPED_MARKETING
                    ):
                        # "The recipient has chosen to stop receiving marketing messages" — the
                        # same fact a `user_preferences` stop reports, arriving on a delivery
                        # failure. Recorded in the same transaction as the fold, so a redelivery
                        # (deduped above by `processed_events`) cannot write it twice.
                        await services.apply_meta_marketing_preference(
                            session,
                            organization_id=organization_id,
                            recipient=update.recipient,
                            opted_out=True,
                            signal=f"error_{update.error_code}",
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
                    if not webhooks.transient_retry_exhausted(update.occurred_at):
                        log.error(
                            "webhook.status_fold_transient_error",
                            provider_message_id=update.provider_message_id,
                            error_type=type(exc).__name__,
                        )
                        had_transient_failure = True
                        continue
                    # Retried for longer than `TRANSIENT_RETRY_CAP` already — asking for yet
                    # another 503 over this one item is a loop that never ends, not a real second
                    # attempt. Parked the same way a permanent error is, rather than held hostage
                    # to a connection problem that has had six hours to clear and has not.
                    log.error(
                        "webhook.status_fold_transient_retry_exhausted",
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
                if services.is_inbound_dedupe_conflict(exc):
                    # Another transaction already durably stored this exact message — the
                    # database's own unique constraint caught the race that `processed_events`
                    # missed (both read "not yet processed" before either committed). Nothing was
                    # lost and nothing needs parking: the message this attempt was trying to store
                    # already exists, so this is success, the same as an ordinary redelivery.
                    log.info(
                        "webhook.redelivered",
                        provider_message_id=inbound.provider_message_id,
                    )
                    if not await _mark_processed(
                        organization_id=organization_id,
                        dedupe_id=inbound.dedupe_id,
                        event_type="whatsapp.message.inbound",
                    ):
                        had_transient_failure = True
                    continue
                if not services.is_permanent_fold_error(exc):
                    if not webhooks.transient_retry_exhausted(inbound.occurred_at):
                        log.error(
                            "webhook.inbound_fold_transient_error",
                            provider_message_id=inbound.provider_message_id,
                            error_type=type(exc).__name__,
                        )
                        had_transient_failure = True
                        continue
                    # See the identical comment in the status loop above — retried longer than
                    # `TRANSIENT_RETRY_CAP` already, parked rather than held hostage forever.
                    log.error(
                        "webhook.inbound_fold_transient_retry_exhausted",
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


async def _mark_processed(
    *, organization_id: uuid.UUID, dedupe_id: uuid.UUID, event_type: str
) -> bool:
    """Record one event as processed in its own transaction — for the one case where the fold's
    own session has already rolled back by the time this runs: the database's own dedupe
    constraint on `inbound_messages` caught a genuine duplicate before `processed_events` did
    (`services.is_inbound_dedupe_conflict`). A fresh `session_scope` is deliberate here for the
    same reason `_dead_letter`'s is.

    `ON CONFLICT DO NOTHING` rather than a plain insert: another loser of the very same race — or
    the winner's own write, if it has not committed yet — may record this event id first, and that
    is success too, not a conflict worth raising on. Returns whether the write itself succeeded,
    the same contract `_dead_letter` uses; the caller treats `False` the same way, as a transient
    failure that makes the whole request 503 so Meta gives this item a genuine second attempt.
    """
    try:
        async with session_scope(organization_id=organization_id) as session:
            stmt = (
                pg_insert(ProcessedEvent)
                .values(event_id=dedupe_id, event_type=event_type)
                .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
            )
            await session.execute(stmt)
    except Exception as exc:
        log.error(
            "webhook.redelivered_mark_failed",
            error_type=type(exc).__name__,
        )
        return False
    return True
