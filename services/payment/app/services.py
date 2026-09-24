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

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, literal, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.config import settings
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
from platform_core.logging import get_logger

log = get_logger("payment.services")


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


def _lease_values(*, now: datetime, attempts: int, lease: timedelta) -> dict[str, Any]:
    """The columns a claim of the right to call the provider writes: one more attempt, a lease
    covering the call, and the earliest moment the next may be claimed."""
    return {
        "attempts": attempts,
        "lease_until": now + lease,
        "next_attempt_at": now + domain.charge_retry_backoff(attempts),
    }


def _lease() -> timedelta:
    return timedelta(seconds=settings().charge_reclaim_after_seconds)


async def stage_charge(
    session: AsyncSession,
    provider: PaymentProvider,
    payload: ChargeIn,
    *,
    now: datetime | None = None,
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

    Returns `(charge, needs_call)`, and `needs_call` is **the right to call the provider**, held
    by at most one caller at a time (D3):

    * a freshly staged charge is inserted already holding it — `attempts = 1` and a lease of
      `charge_reclaim_after_seconds` — and audited `payment.charge.staged`;
    * an existing charge that is still `created` with no provider payment is handed the right only
      if its lease has lapsed and its backoff has passed (`claim_charge_lease`, one conditional
      `UPDATE … RETURNING`). So a caller's HTTP retry of a key whose first call is still in flight
      gets `needs_call=False` and the charge as it stands, instead of a second provider call;
    * a charge that already has a provider payment, or has moved on, is never called again.
    """
    now = now or datetime.now(UTC)
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
            **_lease_values(now=now, attempts=1, lease=_lease()),
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "idempotency_key"])
        .returning(Charge.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        # The key was already used — the charge it created, unchanged. One key, one charge, one
        # collection attempt in flight at a time: a retry must not raise a second payment.
        existing = await selectors.get_charge_by_idempotency_key(
            session, payload.organization_id, payload.idempotency_key
        )
        if existing is None:  # pragma: no cover - the row exists by construction of the conflict
            raise BusinessRuleProblem("charge idempotency conflict with no visible row")
        if existing.state != CHARGE_CREATED or existing.provider_payment_id is not None:
            return existing, False
        claimed = await claim_charge_lease(session, existing.id, now=now)
        return (claimed, True) if claimed is not None else (existing, False)

    charge = await selectors.get_charge(session, inserted_id)
    if charge is None:  # pragma: no cover - just inserted in this transaction
        raise BusinessRuleProblem("charge vanished immediately after insert")
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as="billing",
        action="payment.charge.staged",
        entity="charge",
        entity_id=charge.id,
        after={
            "state": charge.state,
            "amount_minor": charge.amount_minor,
            "currency": charge.currency,
            "invoice_id": str(charge.invoice_id) if charge.invoice_id else None,
        },
    )
    return charge, True


async def claim_charge_lease(
    session: AsyncSession, charge_id: uuid.UUID, *, now: datetime, budget: int | None = None
) -> Charge | None:
    """Take the right to call the provider for one charge, or learn that somebody else has it.

    One conditional `UPDATE … RETURNING`: the row is updated only if the charge is still
    unresolved, no call is in flight (`lease_until` passed), its backoff has passed
    (`next_attempt_at`) and it is inside its attempt budget. Two callers racing it serialize on
    the row, and the second's `WHERE` no longer holds — so an in-flight key can never trigger a
    second call. Returns the charge (re-read) when claimed, else None.

    `budget` is the exclusive upper bound on `attempts` the claim is allowed within — the call
    budget (`charge_max_attempts`) by default, the final-lookup budget beyond it for the sweep's
    last look (`claim_stuck_charge`).
    """
    max_attempts = budget if budget is not None else settings().charge_max_attempts
    lease = _lease()
    stmt = (
        update(Charge)
        .where(
            Charge.id == charge_id,
            Charge.state == CHARGE_CREATED,
            Charge.provider_payment_id.is_(None),
            Charge.deleted_at.is_(None),
            Charge.attempts < max_attempts,
            or_(Charge.lease_until.is_(None), Charge.lease_until <= now),
            or_(Charge.next_attempt_at.is_(None), Charge.next_attempt_at <= now),
        )
        .values(
            attempts=Charge.attempts + 1,
            lease_until=now + lease,
            # `domain.charge_retry_backoff(attempts + 1)`, in SQL: `attempts` on the right-hand
            # side of a SET is the value before this update, so `2 ** attempts` is the new
            # attempt's doubling.
            next_attempt_at=now
            + func.least(
                literal(domain.CHARGE_RETRY_BASE) * func.power(2, Charge.attempts),
                literal(domain.CHARGE_RETRY_CEILING),
            ),
        )
        .returning(Charge)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _receipt(charge: Charge) -> str:
    """The provider Order's `receipt` for a charge: its own id. 36 characters, globally unique,
    and fixed for the charge's life — Razorpay caps `receipt` at 40 characters and does not make
    it unique, so the platform key (`invoice:<uuid>` is 44) would be refused, and a
    per-organization key could collide across tenants on one merchant account."""
    return str(charge.id)


def call_provider_charge(provider: PaymentProvider, charge: Charge) -> ChargeResult:
    """The *second* act, called with no session and therefore no transaction open — a real
    adapter's outbound HTTP happens here and only here.

    **Lookup before create** (ADR-0004 §6, addendum): Razorpay has no idempotency key on a payment
    create, so this never simply creates. It ensures the charge's Order (`receipt` = the charge's
    own id — `_receipt` — found by it if a previous call already made it), asks whether a payment
    already exists against that Order, and only if none does creates one. A call resumed after a
    crash between this and `finalize_charge` therefore lands on the payment the first call made,
    never a second debit.
    """
    notes = _notes_for_charge(charge)
    order = provider.ensure_order(
        receipt=_receipt(charge),
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        notes=notes,
    )
    existing = provider.find_payment(order_id=order.provider_order_id, charge_id=str(charge.id))
    if existing is not None:
        return existing
    return provider.charge(
        idempotency_key=charge.idempotency_key,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        mandate_id=str(charge.mandate_id) if charge.mandate_id else None,
        notes=notes,
        order_id=order.provider_order_id,
    )


async def call_provider_charge_off_loop(
    provider: PaymentProvider, charge: Charge
) -> ChargeResult | None:
    """`call_provider_charge` on a worker thread, bounded by `provider_call_timeout_seconds`, so a
    slow provider never blocks the event loop. None means it did not answer in time: the charge
    stays `created` and its lease expires into the stuck-charge sweep, whose lookup-before-create
    finds whatever the abandoned call achieved (the thread cannot be cancelled, only left)."""
    timeout = settings().provider_call_timeout_seconds
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(call_provider_charge, provider, charge), timeout=timeout
        )
    except TimeoutError:
        log.warning(
            "payment.charge.provider_timeout",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            timeout_seconds=timeout,
        )
        return None


def lookup_provider_charge(provider: PaymentProvider, charge: Charge) -> ChargeResult | None:
    """A **lookup only**: the charge's Order by its receipt, then a payment against it. Never
    creates an Order or a payment — this is the stuck-charge sweep's final look at the attempt
    budget, and its whole point is to find out whether the last attempt's create landed without
    risking another."""
    order = provider.find_order(receipt=_receipt(charge))
    if order is None:
        return None
    return provider.find_payment(order_id=order.provider_order_id, charge_id=str(charge.id))


@dataclass(frozen=True, slots=True)
class LookupOutcome:
    """What the final lookup found: `found` (with the provider's payment), `none` (the provider
    holds no payment for this charge), or `unavailable` (the lookup timed out or raised — nothing
    is known, and nothing may be concluded)."""

    kind: str
    result: ChargeResult | None = None


async def lookup_provider_charge_off_loop(
    provider: PaymentProvider, charge: Charge
) -> LookupOutcome:
    """`lookup_provider_charge` on a worker thread, bounded like every provider call."""
    timeout = settings().provider_call_timeout_seconds
    try:
        found = await asyncio.wait_for(
            asyncio.to_thread(lookup_provider_charge, provider, charge), timeout=timeout
        )
    except TimeoutError:
        log.warning(
            "payment.charge.final_lookup_timeout",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            timeout_seconds=timeout,
        )
        return LookupOutcome("unavailable")
    except Exception:
        log.exception(
            "payment.charge.final_lookup_failed",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
        )
        return LookupOutcome("unavailable")
    return LookupOutcome("found", found) if found is not None else LookupOutcome("none")


async def finalize_charge(
    session: AsyncSession,
    charge_id: uuid.UUID,
    result: ChargeResult,
    *,
    request: Request | None = None,
) -> Charge:
    """Record what the provider said — the *third* act, in its own transaction opened only after
    the call has returned.

    Takes the charge `FOR UPDATE` and re-reads it (`populate_existing`), because a signed webhook
    may have moved it while the call was out — and this step must never undo that:

    * `provider_payment_id` is set **only if it is still NULL**. A different id already recorded
      is an inconsistency between two answers from the provider: it is logged and audited
      (`payment.charge.provider_id_mismatch`) and nothing is overwritten.
    * the state moves **only from `created`**, only along a legal edge, and only to a
      non-terminal state (`authorized`). The terminal captured/failed outcome arrives by webhook
      — as it does in production — and carries its `commerce.payment.*` event; a charge the
      webhook already captured stays captured.
    * the lease is released, and `payment.charge.created` is audited once — by whichever
      recorder first sets the provider payment id — never twice.
    """
    charge = await selectors.lock_charge(session, charge_id)
    if charge is None:  # pragma: no cover - defensive; staging committed before the call ran
        raise BusinessRuleProblem("charge vanished before its result could be recorded")

    first_record = charge.provider_payment_id is None
    if first_record:
        charge.provider_payment_id = result.provider_payment_id
    elif charge.provider_payment_id != result.provider_payment_id:
        log.error(
            "payment.charge.provider_id_mismatch",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
        )
        record_audit(
            session,
            AuditLog,
            organization_id=charge.organization_id,
            actor_id=None,
            acting_as="billing",
            action="payment.charge.provider_id_mismatch",
            entity="charge",
            entity_id=charge.id,
            before={"provider_payment_id": charge.provider_payment_id},
            after={"provider_payment_id": result.provider_payment_id},
            request=request,
        )
    if charge.provider_order_id is None and result.provider_order_id is not None:
        charge.provider_order_id = result.provider_order_id
    if (
        charge.state == CHARGE_CREATED
        and not domain.is_terminal(result.state)
        and domain.is_legal(charge.state, result.state)
    ):
        charge.state = result.state
    charge.lease_until = None

    if first_record:
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


async def claim_stuck_charge(
    session: AsyncSession, charge_id: uuid.UUID, *, now: datetime
) -> tuple[str, Charge | None]:
    """The stuck-charge sweep's first act for one charge, under the tenant binding.

    * `("call", charge)` — inside the call budget (`charge_max_attempts`): the sweep holds the
      lease and may call the provider (lookup before create).
    * `("final_lookup", charge)` — the call budget is spent. The charge is **not** failed here:
      the last attempt's create may have reached the provider with its answer lost, and failing
      it now would tell dunning to debit again. The sweep holds a lease for a **lookup-only**
      call (`lookup_provider_charge`) and decides from what the provider says
      (`fail_exhausted_charge` only if it holds nothing).
    * `("exhausted", charge)` — the final lookup itself failed `charge_final_lookup_attempts`
      times: the charge is failed as `lookup_unavailable`, `commerce.payment.failed` is staged
      and an alert is logged, because a person may need to look at the provider by hand.
    * `("not_due", None)` — resolved meanwhile, locked by somebody else, leased, or backing off.
    """
    charge = await selectors.lock_charge(session, charge_id, skip_locked=True)
    if (
        charge is None
        or charge.state != CHARGE_CREATED
        or charge.provider_payment_id is not None
        or charge.deleted_at is not None
        or (charge.lease_until is not None and charge.lease_until > now)
        or (charge.next_attempt_at is not None and charge.next_attempt_at > now)
    ):
        return "not_due", None
    cfg = settings()
    lookup_budget = cfg.charge_max_attempts + cfg.charge_final_lookup_attempts
    if charge.attempts >= lookup_budget:
        log.error(
            "payment.charge.failed_without_provider_answer",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            attempts=charge.attempts,
            hint="the provider could not be asked whether it holds a payment; check it by hand",
        )
        _fail_locally(session, charge, domain.FAILURE_LOOKUP_UNAVAILABLE)
        await session.flush()
        return "exhausted", charge
    if charge.attempts >= cfg.charge_max_attempts:
        claimed = await claim_charge_lease(session, charge.id, now=now, budget=lookup_budget)
        return ("final_lookup", claimed) if claimed is not None else ("not_due", None)
    claimed = await claim_charge_lease(session, charge.id, now=now)
    return ("call", claimed) if claimed is not None else ("not_due", None)


async def fail_exhausted_charge(session: AsyncSession, charge_id: uuid.UUID) -> Charge | None:
    """The sweep's last act when the final lookup found **no** payment at the provider: re-check,
    under the row lock, that the charge is still `created` with no provider payment (a webhook
    may have landed while the lookup was out), then fail it as `attempts_exhausted` and stage
    `commerce.payment.failed` so dunning proceeds. None when something else resolved it first."""
    charge = await selectors.lock_charge(session, charge_id)
    if charge is None or charge.state != CHARGE_CREATED or charge.provider_payment_id is not None:
        return None
    _fail_locally(session, charge, domain.FAILURE_ATTEMPTS_EXHAUSTED)
    await session.flush()
    return charge


def _fail_locally(session: AsyncSession, charge: Charge, reason: str) -> None:
    """Fail a charge on this platform's own decision — `reason` is one of
    `domain.LOCAL_FAILURE_REASONS`, which is what later lets a provider's late
    `payment.captured` still be recorded (`_reconcile_charge`). Audited and announced."""
    before = charge.state
    charge.state = CHARGE_FAILED
    charge.failure_reason = reason
    charge.lease_until = None
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as="stuck-charge-sweep",
        action="payment.charge.failed",
        entity="charge",
        entity_id=charge.id,
        before={"state": before},
        after={"state": charge.state, "failure_reason": reason, "attempts": charge.attempts},
    )
    _publish_payment_event(session, charge, domain.EVT_FAILED)


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
    caller — the internal route and both consumers — calls `stage_charge`, the provider call and
    `finalize_charge` as three separate acts with **no transaction open across the middle one**;
    see `app/api/internal.py` and `app/consumers.py`.
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
    """Find the charge a webhook is about — by the stamped `charge_id`, else the provider id —
    **row-locked and re-read** (`selectors.lock_charge`). A webhook, `finalize_charge` and the
    stuck-charge sweep all decide from the charge's state; each takes the lock, so none acts on a
    state another has already changed (a second `commerce.payment.failed`, a `captured` written
    over the sweep's commit)."""
    charge_id: uuid.UUID | None = None
    if event.charge_id:
        try:
            charge_id = uuid.UUID(event.charge_id)
        except ValueError:
            charge_id = None
    if charge_id is not None:
        charge = await selectors.lock_charge(session, charge_id)
        if charge is not None:
            return charge
    if event.provider_payment_id:
        found = await selectors.get_charge_by_provider_payment_id(
            session, organization_id, event.provider_payment_id
        )
        if found is not None:
            return await selectors.lock_charge(session, found.id)
    return None


def _record_provider_ids(
    session: AsyncSession, charge: Charge, event: WebhookEvent, *, request: Request | None
) -> None:
    """Take the provider's payment and Order ids from a **verified** webhook when the charge has
    none yet. `finalize_charge` is not the only way a charge learns them: a provider call that
    outlived its timeout was abandoned, its result never recorded, but its capture webhook still
    arrives — and without this the charge would be captured with no provider id forever (no
    refund possible, a `captured` event naming nothing). A *different* id than the one stored is
    logged and audited, never written over."""
    if event.provider_payment_id:
        if charge.provider_payment_id is None:
            charge.provider_payment_id = event.provider_payment_id
            # The first record of the provider's payment, however it arrives — the same single
            # `payment.charge.created` row `finalize_charge` writes when it gets there first.
            record_audit(
                session,
                AuditLog,
                organization_id=charge.organization_id,
                actor_id=None,
                acting_as=f"{event.provider}-webhook",
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
        elif charge.provider_payment_id != event.provider_payment_id:
            log.error(
                "payment.charge.provider_id_mismatch",
                charge_id=str(charge.id),
                organization_id=str(charge.organization_id),
                source="webhook",
            )
            record_audit(
                session,
                AuditLog,
                organization_id=charge.organization_id,
                actor_id=None,
                acting_as=f"{event.provider}-webhook",
                action="payment.charge.provider_id_mismatch",
                entity="charge",
                entity_id=charge.id,
                before={"provider_payment_id": charge.provider_payment_id},
                after={"provider_payment_id": event.provider_payment_id},
                request=request,
            )
    if event.provider_order_id and charge.provider_order_id is None:
        charge.provider_order_id = event.provider_order_id


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

    _record_provider_ids(session, charge, event, request=request)
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
        await session.flush()
        return False

    if (
        charge.state == CHARGE_FAILED
        and target == CHARGE_CAPTURED
        and charge.failure_reason in domain.LOCAL_FAILURE_REASONS
    ):
        # The platform failed this charge on its own judgement (the sweep's budget), and the
        # provider has now said it captured money for it. The money moved; the record follows the
        # money. Refusing here would roll the inbox back and leave a capture nobody ever records.
        log.error(
            "payment.charge.captured_after_local_failure",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            failure_reason=charge.failure_reason,
        )
        before_reason = charge.failure_reason
        charge.state = CHARGE_CAPTURED
        charge.failure_reason = None
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=charge.organization_id,
            actor_id=None,
            acting_as=f"{event.provider}-webhook",
            action="payment.charge.captured_after_local_failure",
            entity="charge",
            entity_id=charge.id,
            before={"state": CHARGE_FAILED, "failure_reason": before_reason},
            after={"state": charge.state, "provider_payment_id": charge.provider_payment_id},
            request=request,
        )
        _publish_payment_event(session, charge, event_type)
        return True

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
            # Additive: why a failed charge failed, so subscription can record it against a trial
            # conversion that the charge beat to the record step (ADR-0005 addendum).
            "failure_reason": charge.failure_reason if charge.state == CHARGE_FAILED else None,
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
