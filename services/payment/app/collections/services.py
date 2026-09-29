"""Writes for tenant collections (ADR-0024 §8): merchant accounts, checkouts, the merchant
webhook, server-to-server status checks, cancellation, refunds and expiry.

**The provider is the source of truth for payment state; these tables are a cache of it.** A
collection moves to `captured` only from a signature-verified merchant webhook or from a
server-to-server status check this service made with the merchant's own keys — never from a
browser's redirect, a Checkout `handler` callback, or anything else a client can say. There is no
route that accepts "the payment succeeded" as input.

**Claim → call → record** (`docs/io-outside-transactions.md`). Every provider call is the middle of
three acts: a short transaction that decides and claims (and opens the merchant's sealed key into
memory for this one call), the call itself on a worker thread with no transaction or row lock
open, and a second short transaction that records the answer under the row lock, re-reading the
row first because a webhook may have moved it meanwhile. The claim functions return a *ticket*;
the call functions take one and never a session.

**Plaintext keys** exist only inside a ticket, for one call, in the process that makes it. They are
never logged, never in an exception message, an event, an audit row or a response.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain as charge_domain
from app import selectors as charge_selectors
from app import services as charge_services
from app.collections import domain, selectors
from app.config import settings
from app.deps import CollectionsProviderFactory
from app.models import (
    CHARGE_CANCELLED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_EXPIRED,
    CHARGE_FAILED,
    HISTORY_SOURCE_API,
    HISTORY_SOURCE_SWEEP,
    HISTORY_SOURCE_SYNC,
    HISTORY_SOURCE_WEBHOOK,
    INBOX_CONFLICT,
    INBOX_IGNORED,
    INBOX_PROCESSED,
    MERCHANT_ACCOUNT_ACTIVE,
    MERCHANT_ACCOUNT_REMOVED,
    MERCHANT_MODE_LIVE,
    PAYEE_MERCHANT,
    REFUND_CREATED,
    REFUND_FAILED,
    AuditLog,
    Charge,
    MerchantAccount,
    OutboxEvent,
    Refund,
    StatusHistory,
)
from app.providers.base import (
    CollectionsProvider,
    MalformedWebhook,
    OrderResult,
    PaymentSnapshot,
    ProviderCallError,
    RefundResult,
)
from app.providers.razorpay import best_attempt
from app.schemas import WebhookAck
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("payment.collections")

#: Which of a merchant account's two sealed secrets — labels (and AAD parts), not credentials.
KEY_SECRET = "key_secret"  # noqa: S105
WEBHOOK_SECRET = "webhook_secret"  # noqa: S105


class ProviderRefusedProblem(ProblemError):
    """The provider answered, and refused — bad merchant keys, a payment it does not know, a
    refund it will not make. 502: the request was fine, the upstream said no. The detail names the
    provider's stable error code only, never its free text."""

    status_code = 502
    problem_type = "provider-refused"
    title = "The payment provider refused the request"


class ProviderUnreachableProblem(UpstreamUnavailableProblem):
    """The provider did not answer in time, or at all. What it did is unknown; every call here is
    safe to repeat (an Order is looked up by receipt before one is created; a refund carries an
    idempotency key), so the answer is "try again"."""


def _now() -> datetime:
    return datetime.now(UTC)


async def bind(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """The first statement of every transaction here: the tenant binding RLS and every
    `WITH CHECK` apply to."""
    await set_tenant(session, organization_id)
    if not settings().is_production:
        await assert_tenant(session, organization_id)


# ============================================================================ merchant accounts


def _seal(organization_id: uuid.UUID, account_id: uuid.UUID, field: str, value: str) -> bytes:
    sealed, _ = pii.seal(
        value,
        kind="generic",
        settings=settings(),
        aad=domain.seal_aad(organization_id, account_id, field),
    )
    return sealed


def _open(account: MerchantAccount, field: str) -> str:
    """One sealed merchant secret, opened for one call. Raises `ConflictProblem` for a removed
    account (its secrets are gone). Never logged; the caller drops it with the ticket."""
    sealed = account.key_secret_enc if field == KEY_SECRET else account.webhook_secret_enc
    if sealed is None or account.status != MERCHANT_ACCOUNT_ACTIVE:
        raise ConflictProblem("This organization's payment provider account is not connected")
    return pii.open_sealed(
        sealed,
        settings=settings(),
        aad=domain.seal_aad(account.organization_id, account.id, field),
    )


def _validate_credentials(provider: str, key_id: str, key_secret: str, webhook_secret: str) -> str:
    """The account's mode, or a 422 naming the field that is wrong — never echoing a secret."""
    if provider not in domain.PROVIDERS:
        raise ValidationProblem(f"{provider!r} is not a supported payment provider")
    mode = domain.mode_for_key_id(key_id)
    if mode is None:
        raise ValidationProblem("key_id must be a Razorpay key id (rzp_test_… or rzp_live_…)")
    if mode == MERCHANT_MODE_LIVE and not settings().collections_allow_live_keys:
        raise ValidationProblem(
            "Live Razorpay keys are not enabled on this platform yet; connect test-mode keys "
            "(rzp_test_…)"
        )
    if not domain.valid_secret(key_secret):
        raise ValidationProblem("key_secret must be 8-128 printable characters with no spaces")
    if not domain.valid_secret(webhook_secret):
        raise ValidationProblem("webhook_secret must be 8-128 printable characters with no spaces")
    return mode


async def set_merchant_account(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    provider: str,
    key_id: str,
    key_secret: str,
    webhook_secret: str,
    request: Request | None = None,
) -> MerchantAccount:
    """Connect the organization's provider account, or rotate its credentials in place.

    Rotation keeps the row — and so the webhook URL registered at the provider, which names the
    row's id. Both secrets are re-sealed on every call (a PUT is the whole credential set), under
    AAD bound to this row, and only their masked hints are kept readable. The audit row names the
    key id and mode — never a secret, its mask, its length or its hash (ADR-0019 §2)."""
    mode = _validate_credentials(provider, key_id, key_secret, webhook_secret)
    now = _now()
    existing = await selectors.lock_live_merchant_account(session, organization_id)
    if existing is not None:
        before = {"provider": existing.provider, "key_id": existing.key_id, "mode": existing.mode}
        existing.provider = provider
        existing.key_id = key_id
        existing.mode = mode
        existing.key_secret_enc = _seal(organization_id, existing.id, KEY_SECRET, key_secret)
        existing.key_secret_masked = domain.mask_secret(key_secret)
        existing.webhook_secret_enc = _seal(
            organization_id, existing.id, WEBHOOK_SECRET, webhook_secret
        )
        existing.webhook_secret_masked = domain.mask_secret(webhook_secret)
        existing.rotated_at = now
        existing.updated_at = now
        existing.updated_by = str(actor_id) if actor_id else None
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=actor_id,
            action="payment.merchant_account.rotated",
            entity="merchant_account",
            entity_id=existing.id,
            before=before,
            after={"provider": provider, "key_id": key_id, "mode": mode},
            request=request,
        )
        return existing

    account_id = uuid.uuid4()
    account = MerchantAccount(
        id=account_id,
        organization_id=organization_id,
        provider=provider,
        key_id=key_id,
        mode=mode,
        key_secret_enc=_seal(organization_id, account_id, KEY_SECRET, key_secret),
        key_secret_masked=domain.mask_secret(key_secret),
        webhook_secret_enc=_seal(organization_id, account_id, WEBHOOK_SECRET, webhook_secret),
        webhook_secret_masked=domain.mask_secret(webhook_secret),
        status=MERCHANT_ACCOUNT_ACTIVE,
        created_by=str(actor_id) if actor_id else None,
        updated_at=now,
    )
    session.add(account)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        raise ConflictProblem(
            "The payment account was connected by someone else just now; reload and try again"
        ) from None
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        action="payment.merchant_account.connected",
        entity="merchant_account",
        entity_id=account.id,
        after={"provider": provider, "key_id": key_id, "mode": mode},
        request=request,
    )
    return account


async def remove_merchant_account(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    request: Request | None = None,
) -> None:
    """Disconnect: the sealed secrets are wiped (not kept "just in case"), the row is
    soft-removed and kept as history. From then on no checkout can be opened, no webhook for it
    verifies, and no status check or refund can be made with it — a later account is a new row
    with a new webhook URL."""
    account = await selectors.lock_live_merchant_account(session, organization_id)
    if account is None:
        raise NotFoundProblem("No payment provider account is connected")
    now = _now()
    account.key_secret_enc = None
    account.webhook_secret_enc = None
    account.key_secret_masked = ""
    account.webhook_secret_masked = ""
    account.status = MERCHANT_ACCOUNT_REMOVED
    account.deleted_at = now
    account.updated_at = now
    account.updated_by = str(actor_id) if actor_id else None
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        action="payment.merchant_account.removed",
        entity="merchant_account",
        entity_id=account.id,
        before={"provider": account.provider, "key_id": account.key_id, "mode": account.mode},
        request=request,
    )


# ============================================================================ shared recording


def _history(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity: str,
    entity_id: uuid.UUID,
    from_state: str | None,
    to_state: str,
    source: str,
    provider_event_id: str | None = None,
    reason: str | None = None,
) -> None:
    session.add(
        StatusHistory(
            organization_id=organization_id,
            entity=entity,
            entity_id=entity_id,
            from_state=from_state,
            to_state=to_state,
            source=source,
            provider_event_id=provider_event_id,
            reason=(reason or None) and reason[:64],
        )
    )


def _collection_payload(charge: Charge) -> dict[str, Any]:
    """What every `payment.collections.payment.*` event carries: identifiers, money and state —
    never a person, never provider free text."""
    return {
        "organization_id": str(charge.organization_id),
        "charge_id": str(charge.id),
        "reference_type": charge.reference_type,
        "reference_id": str(charge.reference_id) if charge.reference_id else None,
        "amount_minor": charge.amount_minor,
        "currency": charge.currency,
        "provider": charge.provider,
        "provider_payment_id": charge.provider_payment_id,
        "provider_order_id": charge.provider_order_id,
        "state": charge.state,
        "occurred_at": _now().isoformat(),
    }


def _publish(session: AsyncSession, charge: Charge, event_type: str, **extra: Any) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=charge.organization_id,
        event_type=event_type,
        aggregate_type="collection",
        aggregate_id=charge.id,
        payload={**_collection_payload(charge), **extra},
        actor={"type": "provider", "id": charge.provider},
    )


def _audit(
    session: AsyncSession,
    charge: Charge,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    acting_as: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=actor_id,
        acting_as=acting_as,
        action=action,
        entity="collection",
        entity_id=charge.id,
        before=before,
        after=after,
        request=request,
    )


async def _flag_double_collection(
    session: AsyncSession, charge: Charge, *, request: Request | None
) -> None:
    """After a collection is captured: is the same order *also* paid by another collection? Two
    checkouts can both be paid (one cancelled or expired here, then paid anyway). Detected,
    logged at error and audited — never refunded automatically; an operator decides which to
    refund (the platform's own double-collection rule, ADR-0004 PAY-A)."""
    if charge.reference_type is None or charge.reference_id is None:
        return
    others = [
        other.id
        for other in await selectors.captured_collections_for_reference(
            session, charge.organization_id, charge.reference_type, charge.reference_id
        )
        if other.id != charge.id
    ]
    if not others:
        return
    other_ids = sorted(str(o) for o in others)
    log.error(
        "payment.collection.collected_twice",
        organization_id=str(charge.organization_id),
        reference_type=charge.reference_type,
        reference_id=str(charge.reference_id),
        charge_id=str(charge.id),
        other_captured_charge_ids=other_ids,
    )
    _audit(
        session,
        charge,
        "payment.collection.collected_twice",
        acting_as="collections",
        after={"other_captured_charge_ids": other_ids, "amount_minor": charge.amount_minor},
        request=request,
    )


async def apply_observation(
    session: AsyncSession,
    charge: Charge,
    observation: domain.Observation,
    *,
    source: str,
    provider_event_id: str | None = None,
    request: Request | None = None,
) -> bool:
    """Apply what the provider says to a locked collection (`domain.decide`), recording the
    status history, the audit row and the event in the caller's transaction. Returns whether an
    event was staged.

    Raises `WebhookNotApplied` for an observation that contradicts the record — another amount or
    currency, a second capture, the captured payment failing — **before** anything is written;
    the caller keeps it (the inbox row, or an audit row for a status check) and changes nothing.
    """
    contradiction = charge_domain.contradiction(
        charge_amount_minor=charge.amount_minor,
        charge_currency=charge.currency,
        event_amount_minor=observation.amount_minor,
        event_currency=observation.currency,
    )
    if contradiction is not None:
        raise contradiction
    decision = domain.decide(charge.state, charge.provider_payment_id, observation)
    acting_as = f"{charge.provider}-{source}"

    if decision.note == "late_authorization":
        log.error(
            "payment.collection.late_authorization",
            organization_id=str(charge.organization_id),
            charge_id=str(charge.id),
            state=charge.state,
        )
        _audit(
            session,
            charge,
            "payment.collection.late_authorization",
            acting_as=acting_as,
            after={"state": charge.state, "provider_payment_id": observation.provider_payment_id},
            request=request,
        )
        return False
    if not decision.changes_anything:
        return False

    before = charge.state
    if decision.bind_payment and observation.provider_payment_id:
        charge.provider_payment_id = observation.provider_payment_id
    if observation.provider_order_id and charge.provider_order_id is None:
        charge.provider_order_id = observation.provider_order_id
    if decision.failure_reason is not None:
        charge.failure_reason = decision.failure_reason
    if decision.clear_failure:
        charge.failure_reason = None
    moved = decision.to_state is not None and decision.to_state != before
    if moved:
        assert decision.to_state is not None
        charge.state = decision.to_state
        charge.lease_until = None
        _history(
            session,
            organization_id=charge.organization_id,
            entity="charge",
            entity_id=charge.id,
            from_state=before,
            to_state=charge.state,
            source=source,
            provider_event_id=provider_event_id,
            reason=observation.failure_code if charge.state == CHARGE_FAILED else decision.note,
        )
        _audit(
            session,
            charge,
            f"payment.collection.{decision.note or charge.state}",
            acting_as=acting_as,
            before={"state": before},
            after={"state": charge.state, "provider_payment_id": charge.provider_payment_id},
            request=request,
        )
        if decision.note and decision.note.startswith("captured_after_"):
            # This platform had stopped waiting (cancelled / expired) and the customer paid
            # anyway. The money moved, so the record follows it — and a person should know.
            log.error(
                f"payment.collection.{decision.note}",
                organization_id=str(charge.organization_id),
                charge_id=str(charge.id),
                reference_id=str(charge.reference_id),
            )
    charge.updated_at = _now()
    await session.flush()
    if moved and decision.emit is not None:
        extra: dict[str, Any] = {}
        if charge.state == CHARGE_FAILED:
            extra["failure_code"] = observation.failure_code
        _publish(session, charge, decision.emit, **extra)
    if moved and charge.state == CHARGE_CAPTURED:
        await _flag_double_collection(session, charge, request=request)
    return moved and decision.emit is not None


# ============================================================================ bounded calls


async def _off_loop[T](fn: Callable[[], T], *, what: str, organization_id: uuid.UUID) -> T:
    """Run one provider call on a worker thread, bounded by `collections_call_timeout_seconds`.
    A timeout or a transport failure is `ProviderUnreachableProblem` (unknown outcome; safe to
    repeat); a provider refusal is `ProviderRefusedProblem` with its stable code only."""
    timeout = settings().collections_call_timeout_seconds
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
    except TimeoutError:
        log.warning(
            "payment.collection.provider_timeout",
            call=what,
            organization_id=str(organization_id),
            timeout_seconds=timeout,
        )
        raise ProviderUnreachableProblem(
            "The payment provider did not answer in time; try again"
        ) from None
    except httpx.HTTPError as exc:
        log.warning(
            "payment.collection.provider_unreachable",
            call=what,
            organization_id=str(organization_id),
            error=type(exc).__name__,
        )
        raise ProviderUnreachableProblem("The payment provider could not be reached") from None
    except ProviderCallError as exc:
        log.warning(
            "payment.collection.provider_refused",
            call=what,
            organization_id=str(organization_id),
            error=str(exc),
        )
        raise ProviderRefusedProblem(str(exc)) from None


@dataclass(frozen=True, slots=True, repr=False)
class ProviderTicket:
    """What a claim hands a call: the merchant's opened keys (for this one call, never logged —
    `repr` is suppressed so a traceback cannot print them) and the identifiers the call needs."""

    organization_id: uuid.UUID
    charge_id: uuid.UUID
    key_id: str
    key_secret: str
    webhook_secret: str
    amount_minor: int
    currency: str
    provider_order_id: str | None
    notes: dict[str, str] = dataclasses.field(default_factory=dict)

    def build(self, factory: CollectionsProviderFactory) -> CollectionsProvider:
        return factory(self.key_id, self.key_secret, self.webhook_secret)


def _ticket(account: MerchantAccount, charge: Charge) -> ProviderTicket:
    return ProviderTicket(
        organization_id=charge.organization_id,
        charge_id=charge.id,
        key_id=account.key_id,
        key_secret=_open(account, KEY_SECRET),
        webhook_secret=_open(account, WEBHOOK_SECRET),
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        provider_order_id=charge.provider_order_id,
        notes={
            "organization_id": str(charge.organization_id),
            "charge_id": str(charge.id),
            "reference_type": charge.reference_type or "",
            "reference_id": str(charge.reference_id) if charge.reference_id else "",
        },
    )


async def _account_for(session: AsyncSession, charge: Charge) -> MerchantAccount:
    """The live account a collection was taken into. Refused (409) when that account has since
    been removed or replaced: its keys are gone, and a different account's keys cannot see a
    payment made into another Razorpay account."""
    account = await selectors.live_merchant_account(session, charge.organization_id)
    if account is None or account.id != charge.merchant_account_id:
        raise ConflictProblem(
            "This payment was taken into a payment provider account that is no longer "
            "connected; check it in that account's own dashboard"
        )
    return account


def _require_reference(
    charge: Charge | None, reference_type: str, reference_id: uuid.UUID
) -> Charge:
    """404 for a collection that is not this reference's — same answer as one that does not
    exist, so an id from another order (or organization) discloses nothing."""
    if (
        charge is None
        or charge.reference_type != reference_type
        or charge.reference_id != reference_id
    ):
        raise NotFoundProblem("No such online payment for this order")
    return charge


def _order_payments(provider: CollectionsProvider, order_id: str) -> list[PaymentSnapshot]:
    return provider.order_payments(order_id=order_id)


async def fetch_order_payments(
    factory: CollectionsProviderFactory, ticket: ProviderTicket
) -> list[PaymentSnapshot]:
    """The server-to-server status check: every attempt on the collection's provider Order, as
    the provider reports it now. No session — the middle act."""
    if ticket.provider_order_id is None:
        return []
    provider = ticket.build(factory)
    order_id = ticket.provider_order_id
    return await _off_loop(
        partial(_order_payments, provider, order_id),
        what="order_payments",
        organization_id=ticket.organization_id,
    )


# ============================================================================ checkout


def _lease() -> timedelta:
    return timedelta(seconds=settings().collection_lease_seconds)


async def _claim_call(session: AsyncSession, charge: Charge, now: datetime) -> bool:
    """Take the right to call the provider for one collection — one conditional `UPDATE`, so two
    operators pressing "collect" at once make one provider call, not two."""
    stmt = (
        update(Charge)
        .where(
            Charge.organization_id == charge.organization_id,
            Charge.id == charge.id,
            Charge.payee == PAYEE_MERCHANT,
            or_(Charge.lease_until.is_(None), Charge.lease_until <= now),
        )
        .values(lease_until=now + _lease(), attempts=Charge.attempts + 1)
        .returning(Charge.id)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


@dataclass(frozen=True, slots=True)
class CheckoutClaim:
    charge_id: uuid.UUID
    #: Set when a provider Order must still be created; None when the checkout is already open
    #: (or another request is creating its Order right now).
    ticket: ProviderTicket | None


async def claim_checkout(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    amount_minor: int,
    currency: str,
    request: Request | None = None,
) -> CheckoutClaim:
    """The claim act of opening a checkout for one order. The amount is whatever the *caller*
    (operations, from the order's own stored total) says — never a browser.

    Refused (409) when the organization has no connected account, when the order is already paid
    online (and not wholly refunded), or when an open collection for a different amount exists.
    Otherwise the one open collection for the order is reused — the same provider Order, which the
    customer may retry against — or a new one is created."""
    if reference_type not in domain.REFERENCE_TYPES:
        raise ValidationProblem(f"{reference_type!r} is not something a payment can be taken for")
    currency = currency.upper()
    account = await selectors.live_merchant_account(session, organization_id)
    if account is None:
        raise ConflictProblem(
            "Online payments are not set up for this organization; connect a Razorpay account "
            "in payment settings first"
        )
    for captured in await selectors.captured_collections_for_reference(
        session, organization_id, reference_type, reference_id
    ):
        refunded = await selectors.refunded_total(session, organization_id, captured.id)
        if refunded < captured.amount_minor:
            raise ConflictProblem("This order has already been paid online")

    charge = await selectors.lock_open_collection(
        session, organization_id, reference_type, reference_id
    )
    if charge is not None and (charge.amount_minor != amount_minor or charge.currency != currency):
        raise ConflictProblem(
            "An online payment for a different amount is already open for this order; cancel it "
            "first"
        )
    if charge is not None and charge.merchant_account_id != account.id:
        raise ConflictProblem(
            "An online payment opened under a previous payment account is still open for this "
            "order; cancel it first"
        )
    if charge is None:
        charge = await _insert_collection(
            session,
            account=account,
            reference_type=reference_type,
            reference_id=reference_id,
            amount_minor=amount_minor,
            currency=currency,
            request=request,
        )
    if charge.provider_order_id is not None:
        return CheckoutClaim(charge_id=charge.id, ticket=None)
    if not await _claim_call(session, charge, _now()):
        return CheckoutClaim(charge_id=charge.id, ticket=None)
    return CheckoutClaim(charge_id=charge.id, ticket=_ticket(account, charge))


async def _insert_collection(
    session: AsyncSession,
    *,
    account: MerchantAccount,
    reference_type: str,
    reference_id: uuid.UUID,
    amount_minor: int,
    currency: str,
    request: Request | None,
) -> Charge:
    charge_id = uuid.uuid4()
    stmt = (
        pg_insert(Charge)
        .values(
            id=charge_id,
            organization_id=account.organization_id,
            payee=PAYEE_MERCHANT,
            merchant_account_id=account.id,
            provider=account.provider,
            amount_minor=amount_minor,
            currency=currency,
            state=CHARGE_CREATED,
            idempotency_key=f"collection:{charge_id}",
            reference_type=reference_type,
            reference_id=reference_id,
            attempts=0,
        )
        # `uq_charges_open_collection`: a concurrent "collect" for the same order already made
        # the open one — use that instead of opening a second checkout.
        .on_conflict_do_nothing()
        .returning(Charge.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is None:
        existing = await selectors.lock_open_collection(
            session, account.organization_id, reference_type, reference_id
        )
        if existing is None:  # pragma: no cover - closed between the conflict and the re-read
            raise ConflictProblem("This order's payment changed just now; try again")
        return existing
    charge = await selectors.lock_collection(session, account.organization_id, inserted)
    assert charge is not None
    _history(
        session,
        organization_id=charge.organization_id,
        entity="charge",
        entity_id=charge.id,
        from_state=None,
        to_state=CHARGE_CREATED,
        source=HISTORY_SOURCE_API,
    )
    _audit(
        session,
        charge,
        "payment.collection.created",
        acting_as="operations",
        after={
            "reference_type": reference_type,
            "reference_id": str(reference_id),
            "amount_minor": amount_minor,
            "currency": currency,
            "mode": account.mode,
        },
        request=request,
    )
    return charge


def _ensure_order(provider: CollectionsProvider, ticket: ProviderTicket) -> OrderResult:
    return provider.ensure_order(
        # The collection's own id: 36 characters (Razorpay caps `receipt` at 40), globally
        # unique, and fixed for the collection's life — what the Order is found again by.
        receipt=str(ticket.charge_id),
        amount_minor=ticket.amount_minor,
        currency=ticket.currency,
        notes=ticket.notes,
    )


async def create_provider_order(
    factory: CollectionsProviderFactory, ticket: ProviderTicket
) -> OrderResult:
    """The call act: the provider Order the customer will pay, looked up by receipt before one
    is created — so a repeat after a lost answer lands on the same Order."""
    provider = ticket.build(factory)
    return await _off_loop(
        partial(_ensure_order, provider, ticket),
        what="ensure_order",
        organization_id=ticket.organization_id,
    )


async def record_checkout(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    provider_order_id: str,
    request: Request | None = None,
) -> Charge:
    """The record act: bind the provider Order (only if none is bound — a different one already
    bound is an inconsistency, logged and audited, never overwritten) and release the lease."""
    charge = await selectors.lock_collection(session, organization_id, charge_id)
    if charge is None:  # pragma: no cover - staged in an earlier committed transaction
        raise NotFoundProblem("No such online payment")
    if charge.provider_order_id is None:
        charge.provider_order_id = provider_order_id
        _audit(
            session,
            charge,
            "payment.collection.checkout_opened",
            acting_as="operations",
            after={"provider_order_id": provider_order_id},
            request=request,
        )
    elif charge.provider_order_id != provider_order_id:
        log.error(
            "payment.collection.provider_order_mismatch",
            organization_id=str(organization_id),
            charge_id=str(charge.id),
        )
        _audit(
            session,
            charge,
            "payment.collection.provider_order_mismatch",
            acting_as="operations",
            before={"provider_order_id": charge.provider_order_id},
            after={"provider_order_id": provider_order_id},
            request=request,
        )
    charge.lease_until = None
    charge.updated_at = _now()
    await session.flush()
    return charge


async def release_call(
    session: AsyncSession, *, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> None:
    """Give the right to call back after the provider **refused** (nothing was created there),
    so the operator can fix the keys and retry at once. Never after a timeout: an abandoned call
    may still land, and the lease is what keeps a retry from racing it."""
    charge = await selectors.lock_collection(session, organization_id, charge_id)
    if charge is not None:
        charge.lease_until = None


# ============================================================================ status check


async def claim_status_check(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    charge_id: uuid.UUID,
) -> ProviderTicket | None:
    """The claim act of a server-to-server status check. None when the collection never reached
    the provider (no Order there to ask about)."""
    charge = _require_reference(
        await selectors.get_collection(session, organization_id, charge_id),
        reference_type,
        reference_id,
    )
    if charge.provider_order_id is None:
        return None
    account = await _account_for(session, charge)
    return _ticket(account, charge)


async def record_status_check(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    payments: Sequence[PaymentSnapshot],
    source: str = HISTORY_SOURCE_SYNC,
    request: Request | None = None,
) -> Charge:
    """The record act: apply what the provider reports about the collection's Order — the
    attempt that says what happened (`best_attempt`) — through the same state machine a webhook
    goes through. A contradiction is audited (`payment.collection.status_conflict`) and changes
    nothing."""
    charge = await selectors.lock_collection(session, organization_id, charge_id)
    if charge is None:  # pragma: no cover - checked by the claim
        raise NotFoundProblem("No such online payment")
    best = best_attempt(list(payments))
    observation = (
        domain.observation_from_snapshot(best, charge.provider_order_id or "")
        if best is not None
        else None
    )
    if observation is not None:
        try:
            async with session.begin_nested():
                await apply_observation(
                    session, charge, observation, source=source, request=request
                )
        except charge_domain.WebhookNotApplied as verdict:
            charge = await selectors.lock_collection(session, organization_id, charge_id) or charge
            log.error(
                "payment.collection.status_conflict",
                organization_id=str(organization_id),
                charge_id=str(charge_id),
                reason=verdict.reason,
            )
            _audit(
                session,
                charge,
                "payment.collection.status_conflict",
                acting_as=f"{charge.provider}-{source}",
                after={"reason": verdict.reason, **verdict.facts},
                request=request,
            )
    return charge


# ============================================================================ cancel


async def record_cancel(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    request: Request | None = None,
) -> Charge:
    """Cancel an unpaid checkout. The caller has already asked the provider (the call act) and
    **committed** what it said (`record_status_check`, its own transaction) — so if the customer
    had paid, the record already says `captured` and this refuses (409) without rolling that
    knowledge back. Re-read under the row lock: a webhook landing in between is seen here too. A
    customer who pays after this anyway is still recorded (`captured_after_cancelled`)."""
    charge = await selectors.lock_collection(session, organization_id, charge_id)
    if charge is None:  # pragma: no cover - checked by the claim
        raise NotFoundProblem("No such online payment")
    if charge.state not in domain.CANCELLABLE_STATES:
        raise ConflictProblem(
            f"This online payment is {charge.state} and cannot be cancelled"
            + (" — the customer has paid" if charge.state == CHARGE_CAPTURED else "")
        )
    before = charge.state
    charge.state = CHARGE_CANCELLED
    charge.lease_until = None
    charge.updated_at = _now()
    _history(
        session,
        organization_id=organization_id,
        entity="charge",
        entity_id=charge.id,
        from_state=before,
        to_state=CHARGE_CANCELLED,
        source=HISTORY_SOURCE_API,
        reason=domain.REASON_CANCELLED_BY_OPERATOR,
    )
    _audit(
        session,
        charge,
        "payment.collection.cancelled",
        actor_id=actor_id,
        acting_as="operations",
        before={"state": before},
        after={"state": CHARGE_CANCELLED},
        request=request,
    )
    await session.flush()
    return charge


async def claim_cancel(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    charge_id: uuid.UUID,
) -> ProviderTicket | None:
    """The claim act of a cancel: refused up front for a collection that cannot be cancelled
    by what this platform already knows; a ticket for the status check otherwise (None when it
    never reached the provider — then there is nothing there to ask)."""
    charge = _require_reference(
        await selectors.get_collection(session, organization_id, charge_id),
        reference_type,
        reference_id,
    )
    if charge.state not in domain.CANCELLABLE_STATES:
        raise ConflictProblem(f"This online payment is {charge.state} and cannot be cancelled")
    if charge.provider_order_id is None:
        return None
    account = await selectors.live_merchant_account(session, organization_id)
    if account is None or account.id != charge.merchant_account_id:
        # Nothing can be asked; nothing could have been paid into a *connected* account either.
        return None
    return _ticket(account, charge)


# ============================================================================ refunds


@dataclass(frozen=True, slots=True, repr=False)
class RefundClaim:
    #: A replay of a refund already recorded under the same key — nothing to call.
    existing: Refund | None
    ticket: ProviderTicket | None
    provider_payment_id: str | None = None
    refund_amount_minor: int = 0
    idempotency_key: str = ""


async def claim_refund(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    charge_id: uuid.UUID,
    amount_minor: int,
    caller_key: str,
) -> RefundClaim:
    """The claim act of a refund: a captured collection, a positive amount, never more than what
    still stands unrefunded. The same caller key is the same refund — here (a replay returns the
    recorded one) and at the provider (`X-Refund-Idempotency`)."""
    provider_key = domain.refund_idempotency_key(organization_id, charge_id, caller_key)
    existing = await selectors.refund_by_idempotency_key(session, organization_id, provider_key)
    if existing is not None:
        return RefundClaim(existing=existing, ticket=None)
    charge = _require_reference(
        await selectors.lock_collection(session, organization_id, charge_id),
        reference_type,
        reference_id,
    )
    if charge.state != CHARGE_CAPTURED or charge.provider_payment_id is None:
        raise ConflictProblem(
            f"Only a paid online payment can be refunded; this one is {charge.state}"
        )
    if amount_minor <= 0:
        raise ValidationProblem("A refund must be a positive amount")
    refunded = await selectors.refunded_total(session, organization_id, charge.id)
    if refunded + amount_minor > charge.amount_minor:
        raise ConflictProblem(
            f"Refunding {amount_minor} would exceed the payment: {refunded} of "
            f"{charge.amount_minor} {charge.currency} minor units are already refunded"
        )
    account = await _account_for(session, charge)
    return RefundClaim(
        existing=None,
        ticket=_ticket(account, charge),
        provider_payment_id=charge.provider_payment_id,
        refund_amount_minor=amount_minor,
        idempotency_key=provider_key,
    )


def _refund(provider: CollectionsProvider, claim: RefundClaim) -> RefundResult:
    assert claim.ticket is not None and claim.provider_payment_id is not None
    return provider.refund(
        idempotency_key=claim.idempotency_key,
        provider_payment_id=claim.provider_payment_id,
        amount_minor=claim.refund_amount_minor,
        currency=claim.ticket.currency,
    )


async def call_refund(factory: CollectionsProviderFactory, claim: RefundClaim) -> RefundResult:
    """The call act. Safe to repeat with the same claim: the provider's refund idempotency key
    makes a retry the same refund."""
    assert claim.ticket is not None
    provider = claim.ticket.build(factory)
    return await _off_loop(
        partial(_refund, provider, claim),
        what="refund",
        organization_id=claim.ticket.organization_id,
    )


async def _insert_refund(
    session: AsyncSession,
    charge: Charge,
    *,
    provider_refund_id: str,
    amount_minor: int,
    state: str,
    reason: str | None,
    idempotency_key: str | None,
) -> tuple[Refund, bool]:
    """Append a merchant refund row, idempotently on the provider's refund id (and the caller's
    key). Returns the row and whether this call inserted it."""
    refund_id = uuid.uuid4()
    stmt = (
        pg_insert(Refund)
        .values(
            id=refund_id,
            organization_id=charge.organization_id,
            charge_id=charge.id,
            provider=charge.provider,
            provider_refund_id=provider_refund_id,
            amount_minor=amount_minor,
            currency=charge.currency,
            state=state,
            reason=(reason or None) and reason[:200],
            payee=PAYEE_MERCHANT,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing()
        .returning(Refund.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    found = await selectors.refund_by_provider_id(
        session, charge.organization_id, provider_refund_id
    )
    if found is None and idempotency_key is not None:
        found = await selectors.refund_by_idempotency_key(
            session, charge.organization_id, idempotency_key
        )
    assert found is not None
    return found, inserted is not None


async def _announce_refunds(
    session: AsyncSession, charge: Charge, row: Refund, refund_state: str
) -> None:
    """Stage `payment.collections.payment.refunded` with the collection's standing refund total —
    what operations reads to reverse (or restore) the order's paid fact."""
    total = await selectors.refunded_total(session, charge.organization_id, charge.id)
    if total > charge.amount_minor:
        # Money moved past what was collected (two refunds racing on different keys, or one made
        # in the provider's own dashboard). The provider refuses this itself, so it should never
        # happen; recorded as it is, and loud.
        log.error(
            "payment.collection.over_refunded",
            organization_id=str(charge.organization_id),
            charge_id=str(charge.id),
            refunded_total_minor=total,
            amount_minor=charge.amount_minor,
        )
    _publish(
        session,
        charge,
        domain.EVT_COLLECTION_REFUNDED,
        refund_id=str(row.id),
        provider_refund_id=row.provider_refund_id,
        refund_amount_minor=row.amount_minor,
        refund_state=refund_state,
        refunded_total_minor=total,
        fully_refunded=total >= charge.amount_minor,
    )


async def record_refund(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    claim: RefundClaim,
    result: RefundResult,
    reason: str | None,
    actor_id: uuid.UUID | None,
    request: Request | None = None,
) -> Refund:
    """The record act: append the refund (append-only — a later provider outcome is a status
    history row), its history row and audit, and announce the collection's new refund total."""
    charge = await selectors.lock_collection(session, organization_id, charge_id)
    if charge is None:  # pragma: no cover - checked by the claim
        raise NotFoundProblem("No such online payment")
    state = result.state or REFUND_CREATED
    row, inserted = await _insert_refund(
        session,
        charge,
        provider_refund_id=result.provider_refund_id,
        amount_minor=claim.refund_amount_minor,
        state=state,
        reason=reason,
        idempotency_key=claim.idempotency_key,
    )
    if not inserted:
        return row
    _history(
        session,
        organization_id=organization_id,
        entity="refund",
        entity_id=row.id,
        from_state=None,
        to_state=state,
        source=HISTORY_SOURCE_API,
    )
    _audit(
        session,
        charge,
        "payment.collection.refunded",
        actor_id=actor_id,
        acting_as="operations",
        after={
            "refund_id": str(row.id),
            "provider_refund_id": row.provider_refund_id,
            "amount_minor": row.amount_minor,
            "state": state,
        },
        request=request,
    )
    await session.flush()
    await _announce_refunds(session, charge, row, state)
    return row


# ============================================================================ the merchant webhook


async def ingest_merchant_webhook(
    session: AsyncSession,
    factory: CollectionsProviderFactory,
    *,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    signature: str | None,
    body: bytes,
    event_id: str | None,
    is_production: bool,
    request: Request | None = None,
) -> WebhookAck:
    """Verify, record and reconcile one merchant webhook. `organization_id` is what the
    pre-tenant lookup found for `account_id` (the URL's merchant account) — the body is not
    trusted for anything, the tenant included, until it verifies against that account's own
    webhook secret.

    1. **Bind** the tenant, read the account under RLS and open its webhook secret (only that
       one: verifying a body needs no key secret). An account removed meanwhile is a 400.
    2. **Verify** the signature over the raw bytes. A bad one is a 400 and nothing is recorded.
    3. **Parse** and **resolve the event id** (the `X-Razorpay-Event-Id` header, else the body's
       own identity) — the platform inbox's rules, unchanged.
    4. **Record and reconcile inside a SAVEPOINT**: the inbox insert is idempotent, so a
       redelivery is recorded once and applied once; the stored body is the redacted one
       (`domain.redact_webhook_body`). A permanent condition — a collection that is not this
       organization's, a contradiction — rolls the attempt back and is recorded as `ignored` or
       `conflict`, audited, and answered 200 (a retry could not change it).
    """
    await set_tenant(session, organization_id)
    if not is_production:
        await assert_tenant(session, organization_id)
    account = await selectors.merchant_account_by_id(session, organization_id, account_id)
    if account is None or account.webhook_secret_enc is None:
        raise charge_services.WebhookSignatureProblem("could not verify the webhook signature")
    provider = factory(account.key_id, "", _open(account, WEBHOOK_SECRET))
    if not provider.verify_webhook(signature=signature, body=body):
        raise charge_services.WebhookSignatureProblem("could not verify the webhook signature")

    try:
        parsed = provider.parse_webhook(body=body)
    except MalformedWebhook as exc:
        log.warning("payment.collection.webhook_malformed", reason=str(exc))
        raise charge_services.WebhookRejectedProblem("the webhook body could not be read") from None
    resolved = charge_domain.resolve_event_id(header=event_id, event=parsed)
    if resolved is None:
        raise charge_services.WebhookRejectedProblem("the webhook carries no usable event id")
    event = dataclasses.replace(parsed, provider_event_id=resolved)
    stored = domain.redact_webhook_body(event.raw)

    charge: Charge | None = None
    refund_facts = (
        domain.refund_facts(event.raw) if event.event_name in domain.REFUND_EVENTS else None
    )
    known_refund: Refund | None = None
    if refund_facts is not None:
        known_refund = await selectors.refund_by_provider_id(
            session, organization_id, refund_facts.provider_refund_id
        )
        if known_refund is not None:
            charge = await selectors.lock_collection(
                session, organization_id, known_refund.charge_id
            )
        elif refund_facts.provider_payment_id:
            charge = await selectors.collection_by_provider_payment(
                session, organization_id, refund_facts.provider_payment_id
            )
    elif event.event_name in domain.COLLECTION_EVENT_OUTCOMES:
        if event.provider_order_id:
            charge = await selectors.collection_by_provider_order(
                session, organization_id, event.provider_order_id
            )
        if charge is None and event.provider_payment_id:
            charge = await selectors.collection_by_provider_payment(
                session, organization_id, event.provider_payment_id
            )
    charge_id = charge.id if charge is not None else None

    try:
        async with session.begin_nested():
            recorded = await _insert_inbox_once(
                session,
                provider,
                organization_id=organization_id,
                event=event,
                charge_id=charge_id,
                signature=signature,
                stored=stored,
            )
            if recorded is None:
                return WebhookAck(
                    received=True,
                    provider_event_id=event.provider_event_id,
                    duplicate=True,
                    reconciled=False,
                )
            event = recorded
            if event.event_name in domain.COLLECTION_EVENT_OUTCOMES:
                if charge is None:
                    raise charge_domain.unknown_charge()
                reconciled = await apply_observation(
                    session,
                    charge,
                    domain.Observation(
                        outcome=domain.COLLECTION_EVENT_OUTCOMES[event.event_name],
                        provider_payment_id=event.provider_payment_id,
                        provider_order_id=event.provider_order_id,
                        amount_minor=event.amount_minor,
                        currency=event.currency,
                        failure_reason=event.failure_reason,
                        failure_code=domain.payment_error_code(event.raw),
                    ),
                    source=HISTORY_SOURCE_WEBHOOK,
                    provider_event_id=event.provider_event_id,
                    request=request,
                )
            elif refund_facts is not None:
                if charge is None:
                    raise charge_domain.unknown_charge()
                reconciled = await _apply_refund_webhook(
                    session,
                    charge,
                    refund_facts,
                    known_refund,
                    provider_event_id=event.provider_event_id,
                    request=request,
                )
            else:
                raise charge_domain.WebhookNotApplied(INBOX_IGNORED, domain.REASON_UNHANDLED_EVENT)
    except charge_domain.WebhookNotApplied as verdict:
        return await charge_services._record_not_applied(
            session,
            organization_id=organization_id,
            event=event,
            charge_id=charge_id,
            signature=signature,
            verdict=verdict,
            request=request,
            payee=PAYEE_MERCHANT,
            payload=stored,
        )
    except IntegrityError as exc:
        if not charge_services._is_provider_id_collision(exc):
            raise
        return await charge_services._record_not_applied(
            session,
            organization_id=organization_id,
            event=event,
            charge_id=charge_id,
            signature=signature,
            verdict=charge_domain.WebhookNotApplied(
                INBOX_CONFLICT, charge_domain.REASON_PROVIDER_ID_CONFLICT
            ),
            request=request,
            payee=PAYEE_MERCHANT,
            payload=stored,
        )
    return WebhookAck(
        received=True,
        provider_event_id=event.provider_event_id,
        duplicate=False,
        reconciled=reconciled,
    )


async def _insert_inbox_once(
    session: AsyncSession,
    provider: CollectionsProvider,
    *,
    organization_id: uuid.UUID,
    event: Any,
    charge_id: uuid.UUID | None,
    signature: str | None,
    stored: dict[str, Any],
) -> Any | None:
    """The idempotent inbox insert with the platform's event-id-reuse guard: the delivery id is
    an unsigned header, so a collision is checked to really be *this* event before it is treated
    as a redelivery. Returns the event as recorded (re-keyed on its own derived id if its
    delivery id was reused), or None for a genuine redelivery."""
    recorded_id = await charge_services._insert_inbox(
        session,
        organization_id=organization_id,
        event=event,
        charge_id=charge_id,
        signature=signature,
        disposition=INBOX_PROCESSED,
        reason=None,
        payee=PAYEE_MERCHANT,
        payload=stored,
    )
    if recorded_id is not None:
        return event
    derived = charge_domain.derive_event_id(event)
    existing = await charge_selectors.get_provider_event(
        session, organization_id, event.provider_event_id
    )
    if (
        derived is None
        or existing is None
        or charge_services._identity_of(provider, existing.payload) in (None, derived)
    ):
        return None
    log.error(
        "payment.collection.webhook_event_id_reused",
        provider_event_id=event.provider_event_id,
        organization_id=str(organization_id),
    )
    rekeyed = dataclasses.replace(event, provider_event_id=derived)
    again = await charge_services._insert_inbox(
        session,
        organization_id=organization_id,
        event=rekeyed,
        charge_id=charge_id,
        signature=signature,
        disposition=INBOX_PROCESSED,
        reason=None,
        payee=PAYEE_MERCHANT,
        payload=stored,
    )
    return rekeyed if again is not None else None


async def _apply_refund_webhook(
    session: AsyncSession,
    charge: Charge,
    facts: domain.RefundFacts,
    known: Refund | None,
    *,
    provider_event_id: str,
    request: Request | None,
) -> bool:
    """A verified refund event. A refund this platform recorded gets its new provider outcome as
    a status-history row (the refund row is append-only). A refund it never asked for — made in
    the merchant's own provider dashboard — is recorded from the provider's word, because the
    money moved and this record follows it. Either way the order hears the new refund total."""
    new_state = domain.refund_state_for_provider_status(facts.status)
    if known is None:
        if facts.amount_minor is None or facts.amount_minor <= 0:
            raise charge_domain.WebhookNotApplied(INBOX_IGNORED, domain.REASON_UNKNOWN_REFUND)
        if facts.currency is not None and facts.currency != charge.currency.upper():
            raise charge_domain.WebhookNotApplied(
                INBOX_CONFLICT,
                charge_domain.REASON_CURRENCY_MISMATCH,
                {"charge_currency": charge.currency, "asserted_currency": facts.currency},
            )
        row, inserted = await _insert_refund(
            session,
            charge,
            provider_refund_id=facts.provider_refund_id,
            amount_minor=facts.amount_minor,
            state=new_state,
            reason="provider_initiated",
            idempotency_key=None,
        )
        if not inserted:  # pragma: no cover - a concurrent delivery of the same refund
            return False
        _history(
            session,
            organization_id=charge.organization_id,
            entity="refund",
            entity_id=row.id,
            from_state=None,
            to_state=new_state,
            source=HISTORY_SOURCE_WEBHOOK,
            provider_event_id=provider_event_id,
            reason="provider_initiated",
        )
        log.warning(
            "payment.collection.refund_made_at_provider",
            organization_id=str(charge.organization_id),
            charge_id=str(charge.id),
        )
        _audit(
            session,
            charge,
            "payment.collection.refund_recorded_from_provider",
            acting_as=f"{charge.provider}-webhook",
            after={
                "refund_id": str(row.id),
                "provider_refund_id": row.provider_refund_id,
                "amount_minor": row.amount_minor,
                "state": new_state,
            },
            request=request,
        )
        await session.flush()
        await _announce_refunds(session, charge, row, new_state)
        return True

    current = (await selectors.effective_refund_states(session, charge.organization_id, [known]))[
        known.id
    ]
    # `pending` from the provider never walks a settled refund back; a settled one only moves on.
    if new_state in (current, REFUND_CREATED):
        return False
    _history(
        session,
        organization_id=charge.organization_id,
        entity="refund",
        entity_id=known.id,
        from_state=current,
        to_state=new_state,
        source=HISTORY_SOURCE_WEBHOOK,
        provider_event_id=provider_event_id,
    )
    if new_state == REFUND_FAILED:
        # The customer was not refunded after all. The order's paid fact comes back (operations
        # reads `fully_refunded` on the event below), and a person should know.
        log.error(
            "payment.collection.refund_failed",
            organization_id=str(charge.organization_id),
            charge_id=str(charge.id),
            refund_id=str(known.id),
        )
    _audit(
        session,
        charge,
        f"payment.collection.refund_{new_state}",
        acting_as=f"{charge.provider}-webhook",
        before={"refund_id": str(known.id), "state": current},
        after={"refund_id": str(known.id), "state": new_state},
        request=request,
    )
    await session.flush()
    await _announce_refunds(session, charge, known, new_state)
    return True


# ============================================================================ expiry


@dataclass(frozen=True, slots=True, repr=False)
class ExpiryClaim:
    charge_id: uuid.UUID
    #: None: nothing to ask (no connected account for it) — expire directly.
    ticket: ProviderTicket | None


async def claim_expiry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    now: datetime,
) -> ExpiryClaim | None:
    """The sweep's claim for one stale checkout, re-checked under the row lock (skip-locked: a
    row somebody else holds is theirs this tick). None when it is no longer due."""
    charge = await selectors.lock_collection(session, organization_id, charge_id, skip_locked=True)
    cutoff = now - timedelta(seconds=settings().collection_checkout_ttl_seconds)
    if (
        charge is None
        or charge.state not in (CHARGE_CREATED, CHARGE_FAILED)
        or charge.created_at > cutoff
        or (charge.lease_until is not None and charge.lease_until > now)
    ):
        return None
    account = await selectors.live_merchant_account(session, organization_id)
    if account is None or account.id != charge.merchant_account_id:
        return ExpiryClaim(charge_id=charge.id, ticket=None)
    charge.lease_until = now + _lease()
    await session.flush()
    return ExpiryClaim(charge_id=charge.id, ticket=_ticket(account, charge))


def _find_order(provider: CollectionsProvider, ticket: ProviderTicket) -> OrderResult | None:
    return provider.find_order(receipt=str(ticket.charge_id))


async def expiry_lookup(
    factory: CollectionsProviderFactory, ticket: ProviderTicket
) -> tuple[str | None, list[PaymentSnapshot]]:
    """The sweep's call act — **lookup only**, never a create: the collection's Order (by receipt
    when an abandoned call may have made one this platform never recorded), then its payments."""
    provider = ticket.build(factory)
    order_id = ticket.provider_order_id
    if order_id is None:
        found = await _off_loop(
            partial(_find_order, provider, ticket),
            what="find_order",
            organization_id=ticket.organization_id,
        )
        order_id = found.provider_order_id if found is not None else None
    if order_id is None:
        return None, []
    payments = await _off_loop(
        partial(_order_payments, provider, order_id),
        what="order_payments",
        organization_id=ticket.organization_id,
    )
    return order_id, payments


async def record_expiry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    provider_order_id: str | None,
    payments: Sequence[PaymentSnapshot],
) -> Charge | None:
    """Close a stale checkout — unless the provider's answer says it was paid (or is being paid),
    in which case that is recorded instead. Returns the charge if it was expired."""
    charge = await selectors.lock_collection(session, organization_id, charge_id)
    if charge is None:
        return None
    if provider_order_id and charge.provider_order_id is None:
        # Flushed now: `record_status_check` re-reads the row with `populate_existing`, which
        # would discard an unflushed attribute.
        charge.provider_order_id = provider_order_id
        await session.flush()
    charge = await record_status_check(
        session,
        organization_id=organization_id,
        charge_id=charge_id,
        payments=payments,
        source=HISTORY_SOURCE_SWEEP,
    )
    if charge.state not in (CHARGE_CREATED, CHARGE_FAILED):
        charge.lease_until = None
        return None
    before = charge.state
    charge.state = CHARGE_EXPIRED
    charge.lease_until = None
    charge.updated_at = _now()
    _history(
        session,
        organization_id=organization_id,
        entity="charge",
        entity_id=charge.id,
        from_state=before,
        to_state=CHARGE_EXPIRED,
        source=HISTORY_SOURCE_SWEEP,
        reason=domain.REASON_CHECKOUT_EXPIRED,
    )
    _audit(
        session,
        charge,
        "payment.collection.expired",
        acting_as="collection-expiry-sweep",
        before={"state": before},
        after={"state": CHARGE_EXPIRED},
    )
    await session.flush()
    return charge


# ============================================================================ reconciliation


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    charge_id: uuid.UUID
    reference_type: str | None
    reference_id: uuid.UUID | None
    state: str
    amount_minor: int
    currency: str
    provider_payment_id: str | None
    provider_order_id: str | None
    refunded_minor: int


@dataclass(frozen=True, slots=True, repr=False)
class ReconciliationClaim:
    ticket: ProviderTicket | None
    items: list[ReconciliationItem]


async def claim_reconciliation(
    session: AsyncSession, *, organization_id: uuid.UUID, limit: int
) -> ReconciliationClaim:
    """What the report will ask the provider about: the most recent collections that reached it,
    taken into the currently connected account. Read-only."""
    account = await selectors.live_merchant_account(session, organization_id)
    if account is None:
        raise ConflictProblem("Online payments are not set up for this organization")
    charges = [
        c
        for c in await selectors.recent_collections_at_provider(
            session, organization_id, limit=limit
        )
        if c.merchant_account_id == account.id
    ]
    items: list[ReconciliationItem] = []
    for charge in charges:
        refunded = await selectors.refunded_total(session, organization_id, charge.id)
        items.append(
            ReconciliationItem(
                charge_id=charge.id,
                reference_type=charge.reference_type,
                reference_id=charge.reference_id,
                state=charge.state,
                amount_minor=charge.amount_minor,
                currency=charge.currency,
                provider_payment_id=charge.provider_payment_id,
                provider_order_id=charge.provider_order_id,
                refunded_minor=refunded,
            )
        )
    ticket = _ticket(account, charges[0]) if charges else None
    return ReconciliationClaim(ticket=ticket, items=items)


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    item: ReconciliationItem
    mismatches: list[str]
    provider_status: str | None
    lookup_failed: bool = False


async def run_reconciliation(
    factory: CollectionsProviderFactory, claim: ReconciliationClaim
) -> list[ReconciliationRow]:
    """The call act of the report: one status check per collection, in sequence, each bounded,
    and the whole report bounded by `collections_reconciliation_budget_seconds` so it answers
    inside the gateway's own timeout. A lookup that fails — or is not reached inside the budget —
    is reported as unknown (`lookup_failed`), never as a mismatch or a match. Nothing is written,
    anywhere."""
    if claim.ticket is None:
        return []
    provider = claim.ticket.build(factory)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings().collections_reconciliation_budget_seconds
    rows: list[ReconciliationRow] = []
    for item in claim.items:
        order_id = item.provider_order_id
        assert order_id is not None
        if loop.time() >= deadline:
            rows.append(ReconciliationRow(item, [], None, lookup_failed=True))
            continue
        try:
            payments = await _off_loop(
                partial(_order_payments, provider, order_id),
                what="order_payments",
                organization_id=claim.ticket.organization_id,
            )
        except (ProviderUnreachableProblem, ProviderRefusedProblem):
            rows.append(ReconciliationRow(item, [], None, lookup_failed=True))
            continue
        best = best_attempt(payments)
        rows.append(
            ReconciliationRow(
                item,
                domain.reconcile_one(
                    state=item.state,
                    amount_minor=item.amount_minor,
                    currency=item.currency,
                    provider_payment_id=item.provider_payment_id,
                    refunded_minor=item.refunded_minor,
                    provider_payments=payments,
                ),
                best.status if best is not None else None,
            )
        )
    return rows
