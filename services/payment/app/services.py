"""Writes for payment — charges, the webhook inbox, reconciliation, and refunds.

The provider seam is the only place a processor's reality enters (ADR-0004 §6): every function
here takes a `PaymentProvider` and speaks its neutral DTOs, so nothing below knows or cares that
the money moved through Razorpay. Persistence, idempotency, the charge state machine and the
outbox event are all real and tested; only the provider's outbound HTTP is stubbed.

Transactions follow the platform discipline (ADR-0002 §1): a route opens the one transaction and
binds the tenant as its first statement. The internal charge route binds the organization named in
the body; the webhook binds the organization the provider echoed in its (signature-verified)
`notes`, because a callback arrives with no tenant of its own. The state change, its audit row and
any `commerce.payment.*` event it stages all commit or all roll back together.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_FAILED,
    MANDATE_ACTIVE,
    REFUND_CREATED,
    AuditLog,
    Charge,
    Mandate,
    OutboxEvent,
    ProviderEvent,
    Refund,
)
from app.providers.base import ChargeResult, PaymentProvider, WebhookEvent
from app.schemas import ChargeIn, WebhookAck
from platform_core.audit import record_audit
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import BusinessRuleProblem, NotFoundProblem, ProblemError


class WebhookSignatureProblem(ProblemError):
    """A provider webhook whose signature does not verify. 400 — the body was not authentic, so it
    is never recorded and never acted on. The message stays vague: a prober learns nothing."""

    status_code = 400
    problem_type = "invalid-webhook-signature"
    title = "Invalid webhook signature"


def _provider_actor(provider: str) -> dict[str, Any]:
    """The provenance stamped on a reconciled event: a provider, not a person and not a service."""
    return {"type": "provider", "id": provider}


def _notes(charge_id: uuid.UUID, payload: ChargeIn) -> dict[str, str]:
    """The metadata stamped onto the provider payment and echoed back on every webhook about it.

    This is what lets a callback name its own tenant before any tenant is bound — the id travels
    with the money and comes back signed. Only strings: `notes` is provider metadata, not a place
    for structured data.
    """
    notes = {"organization_id": str(payload.organization_id), "charge_id": str(charge_id)}
    if payload.subscription_id is not None:
        notes["subscription_id"] = str(payload.subscription_id)
    if payload.product_key is not None:
        notes["product_key"] = payload.product_key
    return notes


def _notes_for_charge(charge: Charge) -> dict[str, str]:
    """The same metadata as `_notes`, read off an already-staged charge rather than the inbound
    payload — what `finalize_charge` and the stuck-charge reconciler call the provider with, since
    neither holds the original `ChargeIn` (the reconciler in particular runs well after the
    request that raised it is gone)."""
    notes = {"organization_id": str(charge.organization_id), "charge_id": str(charge.id)}
    if charge.subscription_id is not None:
        notes["subscription_id"] = str(charge.subscription_id)
    if charge.product_key is not None:
        notes["product_key"] = charge.product_key
    return notes


async def _require_usable_mandate(session: AsyncSession, payload: ChargeIn) -> Mandate | None:
    """The mandate named by a charge, refused unless it can actually authorize the debit.

    models.Mandate states the invariant — "no debit under this mandate may exceed it" — and
    until this guard nothing enforced it: `mandate_id` went straight to the provider without
    a lookup, so a charge could exceed the cap, debit a pending or revoked mandate, cross
    currencies, or name a mandate that does not exist. Every rule is checked here, before a
    charge row exists to reconcile or a provider call is made.
    """
    if payload.mandate_id is None:
        return None
    mandate = await selectors.get_mandate(session, payload.organization_id, payload.mandate_id)
    if mandate is None:
        # Same-shaped answer whether the id is another tenant's or nobody's: not found.
        raise NotFoundProblem("No such mandate")
    if mandate.state != MANDATE_ACTIVE or mandate.activated_at is None:
        raise BusinessRuleProblem(
            f"mandate {mandate.id} is {mandate.state!r} and cannot be debited"
        )
    if mandate.currency != payload.currency.upper():
        raise BusinessRuleProblem(
            f"mandate {mandate.id} is denominated in {mandate.currency}, "
            f"not {payload.currency.upper()}"
        )
    if payload.amount_minor > mandate.max_amount_minor:
        raise BusinessRuleProblem(
            f"amount {payload.amount_minor} exceeds mandate cap {mandate.max_amount_minor}"
        )
    return mandate


async def stage_charge(
    session: AsyncSession,
    provider: PaymentProvider,
    payload: ChargeIn,
) -> tuple[Charge, bool]:
    """Claim a charge idempotently — the *first* of three acts, and the only one with a write
    before the provider is ever asked.

    The insert is `ON CONFLICT DO NOTHING` against `(organization_id, idempotency_key)`, so a
    retried billing run or a redelivered `commerce.invoice.finalized` yields exactly one row. A
    charge that names a mandate is validated against it **before anything is written**: the
    mandate must exist in this organization, be `active` (bank-confirmed, `activated_at` set),
    match the charge currency, and its cap must cover the amount — the model's own stated
    invariant. The mandate id is persisted on the charge so the authority a debit ran under is a
    column, not a reconstruction.

    Returns `(charge, needs_call)`. `needs_call` is true whenever nobody has recorded a provider
    result yet: the ordinary case for a freshly staged charge, and also the case for a charge
    whose staging transaction committed but whose process died before the provider answered or
    before `finalize_charge` recorded it — the caller (the route, the consumer, or the stuck-charge
    reconciler) is expected to call the provider and finalize in that case too. It is exactly the
    platform idempotency key that makes calling the provider again here safe: Razorpay has no
    native one (ADR-0004 §6), so `call_provider_charge` always sends ours, and a second call for
    the same key returns the same provider payment rather than raising a second debit.
    """
    await _require_usable_mandate(session, payload)
    charge_id = uuid.uuid4()
    stmt = (
        pg_insert(Charge)
        .values(
            id=charge_id,
            organization_id=payload.organization_id,
            invoice_id=payload.invoice_id,
            subscription_id=payload.subscription_id,
            product_key=payload.product_key,
            mandate_id=payload.mandate_id,
            provider=provider.name,
            amount_minor=payload.amount_minor,
            currency=payload.currency.upper(),
            state=CHARGE_CREATED,
            idempotency_key=payload.idempotency_key,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "idempotency_key"])
        .returning(Charge.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        # The key was already used — return the charge it created, unchanged. One key, one charge,
        # one collection attempt: a retry must not raise a second payment.
        existing = await selectors.get_charge_by_idempotency_key(
            session, payload.organization_id, payload.idempotency_key
        )
        if existing is None:  # pragma: no cover - the row exists by construction of the conflict
            raise BusinessRuleProblem("charge idempotency conflict with no visible row")
        return existing, existing.provider_payment_id is None

    charge = await selectors.get_charge(session, inserted_id)
    if charge is None:  # pragma: no cover - just inserted in this transaction
        raise BusinessRuleProblem("charge vanished immediately after insert")
    return charge, True


def call_provider_charge(provider: PaymentProvider, charge: Charge) -> ChargeResult:
    """The *second* act — the one stubbed seam — called with no session and therefore no
    transaction open. No keys, no socket in this platform's stub; a real adapter's outbound HTTP
    happens here and only here, never while `stage_charge` or `finalize_charge` hold a connection
    and a row.

    Idempotent on `charge.idempotency_key`, which travels to the provider on every call: a retry
    after a crash between this and `finalize_charge` lands on the provider's own already-created
    payment (Razorpay's `X-Idempotency`/receipt story) rather than a second debit.
    """
    return provider.charge(
        idempotency_key=charge.idempotency_key,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        mandate_id=str(charge.mandate_id) if charge.mandate_id else None,
        notes=_notes_for_charge(charge),
    )


async def finalize_charge(
    session: AsyncSession,
    charge_id: uuid.UUID,
    result: ChargeResult,
    *,
    request: Request | None = None,
) -> Charge:
    """Record what the provider said — the *third* act, in its own transaction opened only after
    the call has returned. The terminal captured/failed outcome is **not** decided here either
    way — it arrives by webhook, as it does in production; this records whatever the synchronous
    create call answered (typically `authorized`).

    Refetches the charge rather than trusting a caller's possibly-stale instance, because the
    stuck-charge reconciler (`app/cron.py`) calls this from a different transaction, sometimes a
    different process, than the one that staged it.
    """
    charge = await selectors.get_charge(session, charge_id)
    if charge is None:  # pragma: no cover - defensive; staging committed before the call ran
        raise BusinessRuleProblem("charge vanished before its result could be recorded")
    charge.provider_payment_id = result.provider_payment_id
    charge.state = result.state

    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as="billing",
        action="payment.charge.created",
        entity="charge",
        entity_id=charge.id,
        after={
            "state": charge.state,
            "amount_minor": charge.amount_minor,
            "provider_payment_id": charge.provider_payment_id,
        },
        request=request,
    )
    await session.flush()
    return charge


async def create_charge(
    session: AsyncSession,
    provider: PaymentProvider,
    payload: ChargeIn,
    *,
    request: Request | None = None,
) -> Charge:
    """All three acts run back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests in `tests/test_mandates_and_refunds.py` that exercise the mandate
    guard against a hand-built session and do not care about the transaction boundary. Every real
    caller — the internal route and both consumers — calls `stage_charge`, `call_provider_charge`
    and `finalize_charge` as three separate acts with **no transaction open across the middle
    one**; see `app/api/internal.py` and `app/consumers.py`.
    """
    charge, needs_call = await stage_charge(session, provider, payload)
    if not needs_call:
        return charge
    result = call_provider_charge(provider, charge)
    return await finalize_charge(session, charge.id, result, request=request)


async def ingest_webhook(
    session: AsyncSession,
    provider: PaymentProvider,
    *,
    signature: str | None,
    body: bytes,
    is_production: bool,
    request: Request | None = None,
) -> WebhookAck:
    """Verify, record and reconcile a provider webhook — the reconciliation entry point.

    Order is load-bearing:

    1. **Verify the signature** over the raw body. A failure is a 400 and nothing is recorded.
    2. **Parse** the (now trusted) body and read the tenant from the echoed `notes`.
    3. **Bind** the transaction to that tenant — the first DB statement — so the append-only inbox
       insert and the charge reconciliation satisfy row-level security.
    4. **Record idempotently.** `ON CONFLICT (organization_id, provider_event_id) DO NOTHING`: a
       re-delivered webhook lands one row and reconciles once. A duplicate acks and stops here.
    5. **Reconcile** the charge and stage `commerce.payment.captured` / `.failed`.
    """
    if not provider.verify_webhook(signature=signature, body=body):
        raise WebhookSignatureProblem("could not verify the webhook signature")

    event = provider.parse_webhook(body=body)
    if not event.organization_id:
        # Authentic (it verified) but unattributable — no tenant to bind, so nothing can be
        # recorded under RLS. A well-formed Razorpay webhook always carries our stamped notes.
        raise BusinessRuleProblem("webhook carries no organization reference in notes")

    organization_id = uuid.UUID(event.organization_id)
    # First statement in the transaction — the internal analogue of the tenancy chokepoint.
    await set_tenant(session, organization_id)
    if not is_production:
        await assert_tenant(session, organization_id)

    # Resolve the charge *before* the inbox insert, so the append-only `provider_events` row can
    # record `charge_id` at insert time — the table is insert-only, so it can never be back-filled.
    charge = await _resolve_charge(session, organization_id, event)

    inbox_id = uuid.uuid4()
    inbox_stmt = (
        pg_insert(ProviderEvent)
        .values(
            id=inbox_id,
            organization_id=organization_id,
            provider=event.provider,
            provider_event_id=event.provider_event_id,
            event_name=event.event_name,
            charge_id=charge.id if charge is not None else None,
            payload=event.raw,
            signature=signature,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "provider_event_id"])
        .returning(ProviderEvent.id)
    )
    recorded_id = (await session.execute(inbox_stmt)).scalar_one_or_none()
    if recorded_id is None:
        # Already in the inbox: a re-delivery. Idempotent — do nothing the first receipt did not.
        return WebhookAck(
            received=True,
            provider_event_id=event.provider_event_id,
            duplicate=True,
            reconciled=False,
        )

    reconciled = await _reconcile_charge(session, charge, event, request=request)
    return WebhookAck(
        received=True,
        provider_event_id=event.provider_event_id,
        duplicate=False,
        reconciled=reconciled,
    )


async def _resolve_charge(
    session: AsyncSession, organization_id: uuid.UUID, event: WebhookEvent
) -> Charge | None:
    """Find the charge a webhook is about — by the stamped `charge_id`, else the provider id."""
    if event.charge_id:
        try:
            charge = await selectors.get_charge(session, uuid.UUID(event.charge_id))
        except ValueError:
            charge = None
        if charge is not None:
            return charge
    if event.provider_payment_id:
        return await selectors.get_charge_by_provider_payment_id(
            session, organization_id, event.provider_payment_id
        )
    return None


async def _reconcile_charge(
    session: AsyncSession,
    charge: Charge | None,
    event: WebhookEvent,
    *,
    request: Request | None,
) -> bool:
    """Move a charge to the outcome a webhook asserts and stage its `commerce.payment.*` event.

    Returns True only when a terminal outcome (`captured` / `failed`) actually moved the charge and
    emitted an event. An unmatched charge, an intermediate `authorized`, or a redelivered terminal
    already in that state all return False — the inbox row is kept either way.
    """
    if charge is None:
        return False

    target = event.outcome_state
    event_type = domain.event_for_outcome(target)

    if event_type is None:
        # An intermediate (`authorized`) or an event this platform does not broadcast. Advance
        # `created -> authorized` if that is what it is, but emit nothing.
        if target == CHARGE_AUTHORIZED and domain.is_legal(charge.state, CHARGE_AUTHORIZED):
            charge.state = CHARGE_AUTHORIZED
            await session.flush()
        return False

    if charge.state == target:
        # A different event id asserting the same terminal outcome — idempotent no-op. Guarded
        # here so the state machine below never sees an illegal `failed -> failed` self-edge.
        return False

    domain.assert_legal(charge.state, target)
    before = charge.state
    charge.state = target
    if target == CHARGE_FAILED and event.failure_reason:
        charge.failure_reason = event.failure_reason[:200]
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as=f"{event.provider}-webhook",
        action=f"payment.charge.{target}",
        entity="charge",
        entity_id=charge.id,
        before={"state": before},
        after={"state": charge.state},
        request=request,
    )
    _publish_payment_event(session, charge, event_type)
    return True


def _publish_payment_event(session: AsyncSession, charge: Charge, event_type: str) -> None:
    """Stage a `commerce.payment.captured` / `.failed` on the outbox, in the charge's transaction.

    The payload carries exactly what the downstream needs and nothing it should not: for a
    failure, `subscription_id` is what the subscription service suspends — the first step of the
    loop that ends in a de-entitled activation and a 402 (ADR-0004 §5).
    """
    from platform_core.events import publish_event

    publish_event(
        session,
        OutboxEvent,
        organization_id=charge.organization_id,
        event_type=event_type,
        aggregate_type="charge",
        aggregate_id=charge.id,
        payload={
            "organization_id": str(charge.organization_id),
            "charge_id": str(charge.id),
            "invoice_id": str(charge.invoice_id) if charge.invoice_id else None,
            "subscription_id": str(charge.subscription_id) if charge.subscription_id else None,
            "product_key": charge.product_key,
            "amount_minor": charge.amount_minor,
            "currency": charge.currency,
            "provider": charge.provider,
            "provider_payment_id": charge.provider_payment_id,
            "state": charge.state,
        },
        actor=_provider_actor(charge.provider),
    )


async def create_refund(
    session: AsyncSession,
    provider: PaymentProvider,
    charge: Charge,
    *,
    amount_minor: int,
    reason: str | None = None,
) -> Refund:
    """Refund a captured charge. Append-only: a refund is a new row, never an edit (ADR-0000 §9).

    Un-exposed for now — there is no customer or operator refund endpoint yet — but the
    docstring's own precondition is enforced rather than advertised: the charge must actually
    be `captured` (a failed or merely-authorized charge keeps its `provider_payment_id`, so
    that alone proved nothing), the amount must be positive, and the running total of
    non-failed refunds may never exceed what was collected.

    The provider idempotency key is derived from the **refund's** identity, not the charge's.
    Keyed on the charge, every partial refund of it replayed the first at the provider while
    the local append-only table recorded a new row with a different amount — a silent,
    permanent divergence between the platform and provider ledgers.
    """
    if charge.state != CHARGE_CAPTURED:
        raise BusinessRuleProblem(
            f"only a captured charge can be refunded; this one is {charge.state!r}"
        )
    if charge.provider_payment_id is None:
        raise BusinessRuleProblem("cannot refund a charge with no provider payment id")
    if amount_minor <= 0:
        raise BusinessRuleProblem("a refund must be a positive amount")
    already_refunded = await selectors.refunded_total_for_charge(session, charge.id)
    if already_refunded + amount_minor > charge.amount_minor:
        raise BusinessRuleProblem(
            f"refunding {amount_minor} would exceed the charge: {already_refunded} of "
            f"{charge.amount_minor} minor units already refunded"
        )

    refund_id = uuid.uuid4()
    result = provider.refund(
        idempotency_key=f"refund:{refund_id}",
        provider_payment_id=charge.provider_payment_id,
        amount_minor=amount_minor,
        currency=charge.currency,
    )
    refund = Refund(
        id=refund_id,
        organization_id=charge.organization_id,
        charge_id=charge.id,
        provider=result.provider,
        provider_refund_id=result.provider_refund_id,
        amount_minor=amount_minor,
        currency=charge.currency,
        state=result.state or REFUND_CREATED,
        reason=reason,
    )
    session.add(refund)
    await session.flush()
    return refund


async def require_charge(session: AsyncSession, charge_id: uuid.UUID) -> Charge:
    charge = await selectors.get_charge(session, charge_id)
    if charge is None:
        # Invisible under RLS unless the transaction is bound to its organization, so another
        # tenant's id is a 404, never a disclosure.
        raise NotFoundProblem("No such charge")
    return charge
