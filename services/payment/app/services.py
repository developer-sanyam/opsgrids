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
    CHARGE_CREATED,
    CHARGE_FAILED,
    REFUND_CREATED,
    AuditLog,
    Charge,
    OutboxEvent,
    ProviderEvent,
    Refund,
)
from app.providers.base import PaymentProvider, WebhookEvent
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


async def create_charge(
    session: AsyncSession,
    provider: PaymentProvider,
    payload: ChargeIn,
    *,
    request: Request | None = None,
) -> Charge:
    """Raise a charge for an invoice, idempotently on the platform-supplied key.

    The insert is `ON CONFLICT DO NOTHING` against `(organization_id, idempotency_key)`, so a
    retried billing run or a redelivered `commerce.invoice.finalized` yields exactly one charge and
    calls the provider exactly once. The provider call (stubbed HTTP) assigns the
    `provider_payment_id` the webhook will later reconcile against; the terminal captured/failed
    outcome is **not** decided here — it arrives by webhook, as it does in production.
    """
    charge_id = uuid.uuid4()
    stmt = (
        pg_insert(Charge)
        .values(
            id=charge_id,
            organization_id=payload.organization_id,
            invoice_id=payload.invoice_id,
            subscription_id=payload.subscription_id,
            product_key=payload.product_key,
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
        return existing

    charge = await selectors.get_charge(session, inserted_id)
    if charge is None:  # pragma: no cover - just inserted in this transaction
        raise BusinessRuleProblem("charge vanished immediately after insert")

    # The one stubbed seam. No keys, no socket — a deterministic fake id keyed on the platform
    # idempotency key, exactly what a real idempotent create would return on a retry.
    result = provider.charge(
        idempotency_key=payload.idempotency_key,
        amount_minor=payload.amount_minor,
        currency=payload.currency.upper(),
        mandate_id=str(payload.mandate_id) if payload.mandate_id else None,
        notes=_notes(charge_id, payload),
    )
    charge.provider_payment_id = result.provider_payment_id
    charge.state = result.state

    record_audit(
        session,
        AuditLog,
        organization_id=payload.organization_id,
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

    Kept minimal and un-exposed for now — there is no customer or operator refund endpoint yet —
    but the table and the provider method exist, so the append-only guarantee is real and tested.
    """
    if charge.provider_payment_id is None:
        raise BusinessRuleProblem("cannot refund a charge with no provider payment id")

    result = provider.refund(
        idempotency_key=f"refund:{charge.id}",
        provider_payment_id=charge.provider_payment_id,
        amount_minor=amount_minor,
        currency=charge.currency,
    )
    refund = Refund(
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
