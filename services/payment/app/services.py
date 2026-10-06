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
import dataclasses
import hashlib
import json
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import func, literal, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.config import settings
from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_FAILED,
    FLOW_CHECKOUT,
    FLOW_DEBIT,
    INBOX_CONFLICT,
    INBOX_PROCESSED,
    MANDATE_ACTIVE,
    PAYEE_PLATFORM,
    REFUND_CREATED,
    AuditLog,
    Charge,
    Mandate,
    OutboxEvent,
    ProviderEvent,
    Refund,
)
from app.providers.base import (
    ChargeResult,
    CollectionsProvider,
    DirectDebitProvider,
    MalformedWebhook,
    PaymentProvider,
    PaymentSnapshot,
    ProviderCallError,
    ProviderCapabilities,
    WebhookEvent,
    capabilities_of,
)
from app.providers.wire import (
    CREDENTIALS,
    CREDENTIALS_LATCH_SECONDS,
    ProviderMisconfigured,
    ProviderRejected,
    ProviderTransient,
    call_deadline,
    ensure_deadline,
)
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


class WebhookRejectedProblem(ProblemError):
    """A signature-verified webhook this service cannot even identify: a body that is not a JSON
    object, or a delivery that yields no usable event id (no `X-Razorpay-Event-Id` header, and a
    body too thin to derive one from). 400, nothing recorded, no state changed — a webhook that
    cannot be told apart from every other one cannot be deduplicated, and keying it on '' would
    swallow every event after the first. Genuine Razorpay deliveries always carry the header, so
    this is a malformed or hand-built request, not a retry storm waiting to happen.

    Every *other* permanent condition is answered 200 and recorded (`ingest_webhook`); this is the
    one that has nothing to record it under."""

    status_code = 400
    problem_type = "invalid-webhook"
    title = "Invalid webhook"


def _actor(event: WebhookEvent) -> str:
    """Who an audit row attributes a reconciled change to: `<provider>-webhook` for a signed
    delivery, `<provider>-sync` for a server-to-server status check (the poller, a checkout's
    read-through, a confirm)."""
    return f"{event.provider}-{event.source}"


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


def debit_refusal(
    capabilities: ProviderCapabilities,
    *,
    amount_minor: int,
    currency: str,
    mandate: Mandate | None,
) -> str | None:
    """Why a debit **cannot succeed at this provider**, as a stable `charges.failure_reason` code —
    or None when it may be attempted. Decided before any provider call, so a charge that could only
    fail at the provider fails here, with no call made and no bank fee paid (ADR-0005 Q4; ADR-0032).

    In order: a currency the provider does not take, an amount below its minimum (Razorpay: 100
    paise — it is a `400` that would burn an attempt), and no usable mandate for a provider whose
    debit needs one (`no_mandate`). A mandate that exists but cannot be debited by this build
    (`supports_mandate_debit` is False for every live adapter in Phase A) is the same answer: the
    customer pays by checkout instead. The legacy stub declares none of this and is never refused.
    """
    if capabilities.currencies is not None and currency.upper() not in capabilities.currencies:
        return domain.FAILURE_UNSUPPORTED_CURRENCY
    if amount_minor < capabilities.min_amount_minor:
        return domain.FAILURE_AMOUNT_BELOW_MINIMUM
    if capabilities.requires_mandate_for_debit and (
        mandate is None or not capabilities.supports_mandate_debit
    ):
        return domain.FAILURE_NO_MANDATE
    return None


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

    **A charge the provider could not take is refused here, before any call** (`debit_refusal`):
    a provider whose debit needs a mandate (`requires_mandate_for_debit` — every live adapter) and
    an organization with no usable one fails as `no_mandate`; so does a currency or an amount below
    the provider's minimum. The row is still written — it is the audit trail, and what dunning's
    `commerce.payment.failed` is about — but it is born `failed`, makes no call, and returns
    `needs_call=False`.

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
    mandate = await _require_usable_mandate(session, payload)
    capabilities = capabilities_of(provider)
    if mandate is None and capabilities.requires_mandate_for_debit:
        # ADR-0005 Q4: the instrument is resolved when the debit is made — the organization's
        # current usable mandate **at this provider**.
        mandate = await selectors.usable_mandate(
            session,
            payload.organization_id,
            currency=payload.currency,
            amount_minor=payload.amount_minor,
            provider=provider.name,
        )
    refusal = debit_refusal(
        capabilities,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        mandate=mandate,
    )
    charge_id = uuid.uuid4()
    stmt = (
        pg_insert(Charge)
        .values(
            id=charge_id,
            organization_id=payload.organization_id,
            invoice_id=payload.invoice_id,
            subscription_id=payload.subscription_id,
            product_key=payload.product_key,
            mandate_id=mandate.id
            if mandate is not None and refusal is None
            else payload.mandate_id,
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
        claimed = await claim_charge_lease(
            session, existing.id, organization_id=payload.organization_id, now=now
        )
        return (claimed, True) if claimed is not None else (existing, False)

    charge = await selectors.get_charge(session, payload.organization_id, inserted_id)
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
    if refusal is not None:
        # Born failed: no provider call is ever made for it, and no attempt was spent.
        charge.attempts = 0
        charge.next_attempt_at = None
        uncollectable = refusal in domain.UNCOLLECTABLE_REASONS
        if uncollectable:
            # Not announced: dunning would suspend a customer for an amount nothing can collect
            # (see `domain.UNCOLLECTABLE_REASONS`). A person decides instead.
            log.error(
                "payment.charge.uncollectable",
                charge_id=str(charge.id),
                organization_id=str(charge.organization_id),
                invoice_id=str(charge.invoice_id) if charge.invoice_id else None,
                failure_reason=refusal,
                amount_minor=charge.amount_minor,
                currency=charge.currency,
                hint="no provider can take this amount; settle the invoice by hand",
            )
        _fail_locally(session, charge, refusal, acting_as="billing", announce=not uncollectable)
        await session.flush()
        return charge, False
    return charge, True


async def claim_charge_lease(
    session: AsyncSession,
    charge_id: uuid.UUID,
    *,
    organization_id: uuid.UUID,
    now: datetime,
    budget: int | None = None,
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
            Charge.organization_id == organization_id,
            Charge.id == charge_id,
            # The platform's right-to-call lease only: a merchant charge is never debited by the
            # platform's provider (`app/collections/` claims its own), and a checkout charge is
            # never "called" at all — it is opened by the customer (`app/checkout/`).
            Charge.payee == PAYEE_PLATFORM,
            Charge.flow == FLOW_DEBIT,
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
    and fixed for the charge's life — Razorpay caps `receipt` at 40 characters, so the platform key
    (`invoice:<uuid>` is 44) would be refused, and a per-organization key could collide across
    tenants on one merchant account. Razorpay does **not** keep a receipt unique (a duplicate create
    is accepted, observed 2026-10-06) and its receipt search can miss a fresh Order, so the receipt
    is a best-effort handle for finding an Order whose id was never recorded, not an idempotency
    key (`RazorpayProvider.ensure_order`). Cashfree calls the same handle the Order's `order_id`,
    and does keep it unique."""
    return str(charge.id)


def _call_direct_debit(provider: DirectDebitProvider, charge: Charge) -> ChargeResult:
    """The debit path of a provider with **no Order step** (`debit_shape = "direct"`; Cashfree
    subscriptions, Phase B): find a debit already made for this charge, create one only if none
    was. Same invariant as the Order-then-charge path, one request shorter. Declared in Phase A —
    no live adapter reaches it, because a debit needs a mandate and no mandate is debitable yet —
    but the branch exists so that the first one that does is held to `DirectDebitContract`, not to
    whatever this function happens to do."""
    existing = provider.find_debit(charge_ref=_receipt(charge))
    if existing is not None:
        return existing
    return provider.debit(
        idempotency_key=charge.idempotency_key,
        charge_ref=_receipt(charge),
        mandate_id=str(charge.mandate_id) if charge.mandate_id else None,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        notes=_notes_for_charge(charge),
    )


def call_provider_charge(provider: PaymentProvider, charge: Charge) -> ChargeResult:
    """The *second* act, called with no session and therefore no transaction open — a real
    adapter's outbound HTTP happens here and only here.

    **Lookup before create** (ADR-0004 §6, addendum): Razorpay has no idempotency key on a payment
    create, so this never simply creates. It ensures the charge's Order (`receipt` = the charge's
    own id — `_receipt` — found by it if a previous call already made it), asks whether a payment
    already exists against that Order, and only if none does creates one. A call resumed after a
    crash between this and `finalize_charge` therefore lands on the payment the first call made,
    never a second debit. A provider with no Order step (`debit_shape = "direct"`) is the same
    promise in two requests (`_call_direct_debit`).

    **Only as good as the provider's receipt index.** The stub's is exact, so this holds there
    (`ProviderContract`). Razorpay's live receipt search can miss an Order created seconds earlier
    (ADR-0032, 2026-10-06), so a live order-then-charge debit — Phase B; no live adapter reaches
    this today, a platform debit is refused `no_mandate` before any call — must record the Order id
    in its own transaction before it creates the payment, and resume from the recorded id.

    The whole call runs under an overall monotonic deadline (`provider_call_timeout_seconds`) that
    the live wire clamps every request to — a per-request timeout alone restarts whenever bytes
    arrive and a call makes several requests (ADR-0004 addendum, B5g caveat 1).
    """
    with ensure_deadline(settings().provider_call_timeout_seconds):
        if capabilities_of(provider).debit_shape == "direct":
            return _call_direct_debit(cast(DirectDebitProvider, provider), charge)
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


def provider_for_charge(default: PaymentProvider, charge: Charge) -> PaymentProvider:
    """The adapter that finishes **this charge**: the default when it is the provider the charge was
    created under (so the adapter a caller or a test built keeps its own state), otherwise the
    registry's adapter for the charge's own provider. A retry of an idempotency key must not send a
    Razorpay charge to Cashfree because the default moved (ADR-0032)."""
    if default.name == charge.provider:
        return default
    from app.deps import resolve_provider

    return resolve_provider(charge.provider)


#: What a provider call came to, as the service layer treats it (see `CallOutcome`).
OUTCOME_RESULT = "result"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_REJECTED = "rejected"
OUTCOME_MISCONFIGURED = "misconfigured"


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """The three-way taxonomy at the seam (ADR-0032), plus success:

    * `result` — the provider answered; `result` is what it said. Recorded (`finalize_charge`).
    * `unknown` — a timeout, a connect failure, a `429`/`5xx`, an unreadable answer: the call may or
      may not have landed. **Nothing is recorded and nothing is concluded**; the charge stays
      `created` under its lease and the sweep's lookup-before-create finds whatever happened.
    * `rejected` — the provider refused (any other `4xx`, an unbuilt debit): provably nothing was
      created. The charge **fails**, with the provider's stable `code` as its reason, and dunning
      proceeds.
    * `misconfigured` — the platform's own credentials are wrong or not allowed. **Never the
      charge's fault**: it is not failed, `commerce.payment.failed` is not emitted, and the attempt
      the claim took is given back — an operator is paged instead (`CREDENTIALS` latch). Dunning a
      paying tenant for the platform's bad key is the failure this exists to prevent.
    """

    kind: str
    result: ChargeResult | None = None
    code: str | None = None

    @property
    def needs_record(self) -> bool:
        """Whether a short transaction must follow to record what happened."""
        return self.kind != OUTCOME_UNKNOWN


def _credentials_rejected(provider: str, code: str | None, charge: Charge | None = None) -> None:
    """The first rejected-credentials answer trips the latch and pages (an `error` line an alert
    matches on); later ones inside the window are a `warning`, so one outage is one page."""
    fields: dict[str, Any] = {"provider": provider, "code": code}
    if charge is not None:
        fields["charge_id"] = str(charge.id)
        fields["organization_id"] = str(charge.organization_id)
    if CREDENTIALS.trip(provider):
        log.error(
            "payment.provider.credentials_rejected",
            hint="the platform's own provider credentials were refused; check the keys and the "
            "PAYMENT_WIRE / CASHFREE_ENV switch on the box. No charge is failed and no attempt "
            "is consumed until a call succeeds again.",
            **fields,
        )
    else:
        log.warning("payment.provider.credentials_rejected_again", **fields)


async def attempt_provider_charge(provider: PaymentProvider, charge: Charge) -> CallOutcome:
    """`call_provider_charge` on a worker thread, bounded by `provider_call_timeout_seconds`, with
    every way it can end classified (`CallOutcome`) — so a slow provider never blocks the event loop
    and a failure is never mistaken for a different one.

    The overall deadline starts here, with the caller's own wait, and follows the call onto the
    thread. While the credentials latch is tripped no call is made at all."""
    name = provider.name
    if CREDENTIALS.tripped(name):
        return CallOutcome(OUTCOME_MISCONFIGURED, code="credentials_rejected")
    timeout = settings().provider_call_timeout_seconds
    try:
        with call_deadline(timeout):
            result = await asyncio.wait_for(
                asyncio.to_thread(call_provider_charge, provider, charge), timeout=timeout
            )
    except TimeoutError:
        log.warning(
            "payment.charge.provider_timeout",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            timeout_seconds=timeout,
        )
        return CallOutcome(OUTCOME_UNKNOWN, code="timeout")
    except ProviderMisconfigured as exc:
        _credentials_rejected(name, exc.code, charge)
        return CallOutcome(OUTCOME_MISCONFIGURED, code=exc.code)
    except ProviderRejected as exc:
        log.warning(
            "payment.charge.provider_rejected",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            provider=name,
            code=exc.code,
            status=exc.status_code,
        )
        return CallOutcome(OUTCOME_REJECTED, code=exc.code or f"http_{exc.status_code}")
    except NotImplementedError:
        # An unbuilt debit (Phase B) reached the wire: nothing was created, and no retry would
        # change that — a rejection, with a code that says what it is.
        return CallOutcome(OUTCOME_REJECTED, code="debit_not_supported")
    except (ProviderTransient, ProviderCallError, httpx.HTTPError) as exc:
        log.warning(
            "payment.charge.provider_unknown_outcome",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            provider=name,
            error=type(exc).__name__,
            code=getattr(exc, "code", None),
        )
        return CallOutcome(OUTCOME_UNKNOWN, code=getattr(exc, "code", None))
    return CallOutcome(OUTCOME_RESULT, result=result)


async def call_provider_charge_off_loop(
    provider: PaymentProvider, charge: Charge
) -> ChargeResult | None:
    """`attempt_provider_charge`, reduced to the answer: the provider's result, or None for every
    other outcome. Kept for callers (and tests) that only care whether the provider answered; the
    three real callers use `attempt_provider_charge` and `record_charge_outcome`, which tell a
    timeout from a rejection from a rejected key."""
    return (await attempt_provider_charge(provider, charge)).result


async def record_charge_outcome(
    session: AsyncSession,
    charge_id: uuid.UUID,
    outcome: CallOutcome,
    *,
    organization_id: uuid.UUID,
    request: Request | None = None,
) -> Charge | None:
    """The *third* act for any outcome that needs one, in its own transaction opened only after the
    call returned. `result` is `finalize_charge`. `rejected` fails the charge (re-checked under the
    row lock: a webhook may have moved it meanwhile) with `provider_rejected:<code>` as its reason
    and stages `commerce.payment.failed`. `misconfigured` gives the claimed attempt back and holds
    the next try off for the latch window — the charge stays `created`, nothing is announced.
    `unknown` has nothing to record. None when something else resolved the charge first."""
    if outcome.kind == OUTCOME_RESULT and outcome.result is not None:
        return await finalize_charge(
            session, charge_id, outcome.result, organization_id=organization_id, request=request
        )
    if outcome.kind == OUTCOME_UNKNOWN:
        return await selectors.get_charge(session, organization_id, charge_id)
    charge = await selectors.lock_charge(session, organization_id, charge_id)
    if (
        charge is None
        or charge.payee != PAYEE_PLATFORM
        or charge.state != CHARGE_CREATED
        or charge.provider_payment_id is not None
    ):
        return None
    if outcome.kind == OUTCOME_REJECTED:
        reason = (domain.FAILURE_PROVIDER_REJECTED_PREFIX + (outcome.code or "unknown"))[:200]
        _fail_locally(session, charge, reason, acting_as="billing")
    else:
        charge.attempts = max(charge.attempts - 1, 0)
        charge.lease_until = None
        charge.next_attempt_at = datetime.now(UTC) + timedelta(seconds=CREDENTIALS_LATCH_SECONDS)
        log.warning(
            "payment.charge.attempt_returned",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            reason="credentials_rejected",
        )
    await session.flush()
    return charge


def lookup_provider_charge(provider: PaymentProvider, charge: Charge) -> ChargeResult | None:
    """A **lookup only**: the charge's Order by its receipt, then a payment against it (or, for a
    provider with no Order step, the debit already made). Never creates an Order or a payment —
    this is the stuck-charge sweep's final look at the attempt budget, and its whole point is to
    find out whether the last attempt's create landed without risking another."""
    with ensure_deadline(settings().provider_call_timeout_seconds):
        if capabilities_of(provider).debit_shape == "direct":
            return cast(DirectDebitProvider, provider).find_debit(charge_ref=_receipt(charge))
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
        with call_deadline(timeout):
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
    except ProviderMisconfigured as exc:
        _credentials_rejected(provider.name, exc.code, charge)
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
    organization_id: uuid.UUID,
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
    charge = await selectors.lock_charge(session, organization_id, charge_id)
    if charge is None:  # pragma: no cover - defensive; staging committed before the call ran
        raise BusinessRuleProblem("charge vanished before its result could be recorded")
    if result.provider != charge.provider:
        # A payment made at one provider must never be recorded against a charge that belongs to
        # another: it would bind an id of the wrong namespace, and every later lookup would ask
        # the wrong provider about it. A defect in whoever routed the call (ADR-0032).
        log.error(
            "payment.charge.provider_mismatch",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
            charge_provider=charge.provider,
            result_provider=result.provider,
        )
        raise BusinessRuleProblem("the provider's result is not for this charge's provider")

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
    session: AsyncSession, charge_id: uuid.UUID, *, organization_id: uuid.UUID, now: datetime
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
    charge = await selectors.lock_charge(session, organization_id, charge_id, skip_locked=True)
    if (
        charge is None
        or charge.payee != PAYEE_PLATFORM
        or charge.flow != FLOW_DEBIT
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
        claimed = await claim_charge_lease(
            session, charge.id, organization_id=organization_id, now=now, budget=lookup_budget
        )
        return ("final_lookup", claimed) if claimed is not None else ("not_due", None)
    claimed = await claim_charge_lease(session, charge.id, organization_id=organization_id, now=now)
    return ("call", claimed) if claimed is not None else ("not_due", None)


async def fail_exhausted_charge(
    session: AsyncSession, charge_id: uuid.UUID, *, organization_id: uuid.UUID
) -> Charge | None:
    """The sweep's last act when the final lookup found **no** payment at the provider: re-check,
    under the row lock, that the charge is still `created` with no provider payment (a webhook
    may have landed while the lookup was out), then fail it as `attempts_exhausted` and stage
    `commerce.payment.failed` so dunning proceeds. None when something else resolved it first."""
    charge = await selectors.lock_charge(session, organization_id, charge_id)
    if (
        charge is None
        or charge.payee != PAYEE_PLATFORM
        or charge.flow != FLOW_DEBIT
        or charge.state != CHARGE_CREATED
        or charge.provider_payment_id is not None
    ):
        return None
    _fail_locally(session, charge, domain.FAILURE_ATTEMPTS_EXHAUSTED)
    await session.flush()
    return charge


def _fail_locally(
    session: AsyncSession,
    charge: Charge,
    reason: str,
    *,
    acting_as: str = "stuck-charge-sweep",
    announce: bool = True,
) -> None:
    """Fail a charge on this platform's own decision — `reason` is a stable code (one of
    `domain.LOCAL_FAILURE_REASONS` when a provider's late `payment.captured` may still turn out to
    be real, which is what lets `_reconcile_charge` record it; a refusal made before any call, like
    `no_mandate`, can have no such payment). Audited, and announced (`commerce.payment.failed`)
    unless `announce` is False — a refusal no checkout can cure (`domain.UNCOLLECTABLE_REASONS`)
    must not start dunning. The audit row says which (`announced`)."""
    before = charge.state
    charge.state = CHARGE_FAILED
    charge.failure_reason = reason
    charge.lease_until = None
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as=acting_as,
        action="payment.charge.failed",
        entity="charge",
        entity_id=charge.id,
        before={"state": before},
        after={
            "state": charge.state,
            "failure_reason": reason,
            "attempts": charge.attempts,
            "announced": announce,
        },
    )
    if announce:
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
    return await finalize_charge(
        session, charge.id, result, organization_id=payload.organization_id, request=request
    )


#: The unique indexes on `charges` that a webhook's provider ids can collide with: another charge of
#: the same organization already holds the payment id, or the Order id, the webhook asserts for this
#: one. Contradictory data, permanent — see `ingest_webhook`.
_PROVIDER_ID_INDEXES = ("uq_charges_org_provider_payment_id", "uq_charges_org_provider_order_id")


def _organization_of(event: WebhookEvent) -> uuid.UUID | None:
    """The organization a verified webhook names in its echoed `notes`, or None when it names none
    (a payment made on the same provider account outside this platform) or names something that is
    not an id. Either way there is no tenant to bind."""
    if not event.organization_id:
        return None
    try:
        return uuid.UUID(event.organization_id)
    except ValueError:
        return None


def _log_unattributable(event: WebhookEvent) -> None:
    """Log a verified webhook that names no usable organization, at the level its cause deserves.

    Such a webhook cannot be recorded (no tenant to bind), so this line is the whole trace of it. A
    payment made on the same provider account outside the platform carries none of our notes: that
    is `warning`, ordinary noise. But a body that carries **our own** notes half-formed — a
    `charge_id` with no organization, or an organization that is not an id — is a platform-side bug
    malforming what it stamped, and the 200 we answer would otherwise lose a real capture
    silently: that is `error`, with a reason code (and our own charge id, when it is one) so an
    operator can find the payment."""
    fields: dict[str, Any] = {
        "provider_event_id": event.provider_event_id,
        "event_name": event.event_name,
    }
    if event.organization_id:
        reason, emit = "organization_id_malformed", log.error
    elif event.charge_id:
        reason, emit = "charge_named_without_organization", log.error
    else:
        reason, emit = "no_platform_notes", log.warning
    fields["reason"] = reason
    if event.charge_id:
        # Only when it is one of our ids: anything else is nothing an operator could look up.
        with suppress(ValueError):
            fields["charge_id"] = str(uuid.UUID(event.charge_id))
    emit("payment.webhook.unattributable", **fields)


def _is_provider_id_collision(exc: IntegrityError) -> bool:
    """True iff `exc` is one of `_PROVIDER_ID_INDEXES` firing — a webhook asserting a provider id
    another charge already holds. Any other integrity error is a bug or a race, and stays one."""
    message = str(exc.orig)
    return any(name in message for name in _PROVIDER_ID_INDEXES)


def _identity_of(
    provider: PaymentProvider | CollectionsProvider, payload: dict[str, Any]
) -> str | None:
    """The derived identity (`domain.derive_event_id`) of a **stored** webhook body — what the event
    it recorded actually was, whatever delivery id it was filed under. Read back from JSONB, so key
    order and number formatting may differ from the bytes Razorpay sent; the identity is built
    only from the event name, entity id and integer timestamp, which JSONB round-trips exactly.
    None when the stored body cannot be read as an event (then it is not evidence of a reuse)."""
    try:
        return domain.derive_event_id(provider.parse_webhook(body=json.dumps(payload).encode()))
    except MalformedWebhook:
        return None


def _stored_payload(
    provider: PaymentProvider, event: WebhookEvent, headers: Mapping[str, str] | None
) -> dict[str, Any] | None:
    """What the inbox stores of this event, or None for the verbatim body (an adapter with no
    redactor). The redacted body must still be readable by `provider.parse_webhook`
    (`_identity_of` re-derives the event's identity from it); the adapters' allow-lists keep every
    field that identity is built from. A header-signed adapter's webhook version is noted in a
    `_meta` key the parser ignores — it is how a later payload-shape change would be recognised."""
    redact = getattr(provider, "redact_webhook", None)
    if not callable(redact):
        return None
    stored: dict[str, Any] = redact(event.raw)
    version = (headers or {}).get("x-webhook-version")
    if version:
        stored["_meta"] = {"webhook_version": version[:20]}
    return stored


async def _insert_inbox(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event: WebhookEvent,
    charge_id: uuid.UUID | None,
    signature: str | None,
    disposition: str,
    reason: str | None,
    payee: str = PAYEE_PLATFORM,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """The idempotent inbox insert: `ON CONFLICT (organization_id, provider_event_id) DO NOTHING`.
    Returns the new row's id, or None when the event is already there — a redelivery. The table is
    insert-only, so `disposition` and `reason` are stated here, at the one moment they can be.

    `payload` overrides what is stored (the verbatim body by default): a merchant webhook stores
    the reduced body `app.collections.domain.redact_webhook_body` returns, never the tenant's
    customer's personal data (see `ProviderEvent.payee`)."""
    stmt = (
        pg_insert(ProviderEvent)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            provider=event.provider,
            provider_event_id=event.provider_event_id,
            event_name=event.event_name,
            charge_id=charge_id,
            payload=event.raw if payload is None else payload,
            signature=signature,
            disposition=disposition,
            disposition_reason=reason,
            payee=payee,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "provider", "provider_event_id"])
        .returning(ProviderEvent.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def ingest_webhook(
    session: AsyncSession,
    provider: PaymentProvider,
    *,
    signature: str | None,
    body: bytes,
    is_production: bool,
    event_id: str | None = None,
    request: Request | None = None,
    headers: Mapping[str, str] | None = None,
) -> WebhookAck:
    """Verify, record and reconcile a provider webhook — the reconciliation entry point.

    `event_id` is the delivery id the transport carried — Razorpay's `X-Razorpay-Event-Id` header,
    which its body does not repeat. It is **not** signed (the HMAC covers the body only), so it is a
    delivery id and nothing more; see `domain.resolve_event_id`.

    `headers` are the request's headers, passed **only** to an adapter that declares
    `signs_headers` (Cashfree signs a timestamp header plus the body, so its verification needs it).
    A Razorpay delivery is verified exactly as before, from `signature` and `body` alone.

    What is stored of a verified body is what the adapter's `redact_webhook` leaves of it — an
    allow-list: the platform inbox is append-only, and a real payment entity carries an email, a
    phone, a VPA and card hints that could then never be erased. (An adapter with no redactor, a
    test double, stores the body as it always did.)

    Order is load-bearing:

    1. **Verify the signature** over the raw body. A failure is a 400 and nothing is recorded.
    2. **Parse** the (now trusted) body and **resolve the event id** the inbox is idempotent on:
       the header, else an id the body carries, else a digest of the body's event name, entity id
       and timestamp. A webhook that yields none is refused (400) — never keyed on `''`, which
       would fold every event an organization receives into one.
    3. **Attribute** it: the tenant comes from the echoed `notes`. One that names none (a payment
       made on the same provider account outside this platform) has no tenant to record under and
       cannot be recorded; it is logged (`error` if it carries our own notes half-formed, `warning`
       otherwise) and answered 200, because the provider redelivering it can never make it
       attributable.
    4. **Bind** the transaction to that tenant — the first DB statement — so the append-only inbox
       insert and the charge reconciliation satisfy row-level security.
    5. **Record and reconcile inside a SAVEPOINT.** The inbox insert is idempotent (`ON CONFLICT
       DO NOTHING`): a redelivery lands one row, reconciles once, and a duplicate acks and stops —
       after checking that the row it collided with is *this event* (the delivery id is unsigned;
       a different signed body filed under it is re-keyed on its own derived id, error-logged
       `payment.webhook.event_id_reused`, and processed).
       If reconciliation meets a **permanent** condition — a charge that is not this
       organization's (the lookup filters on the organization, not on RLS alone), an outcome the
       state machine forbids, another amount or currency, a payment id other than the one the
       charge holds — it
       raises `domain.WebhookNotApplied`; the SAVEPOINT rolls back the attempt (the inbox row and
       any partial write with it) and the event is recorded once more, this time as `ignored` or
       `conflict` with a stable reason code, audited (`payment.webhook.ignored`), logged, and
       answered **200**. A 4xx here would make the provider redeliver, for days, an event that can
       never succeed — and record nothing. Only a bad signature (400), an unidentifiable webhook
       (400) and a *transient* failure (the database, surfaced by the route as 503) are non-2xx;
       a transient failure rolls the whole transaction back, so the retry starts clean. Any other
       exception is a defect and propagates.
    """
    if capabilities_of(provider).signs_headers:
        # Fails closed inside the adapter: no timestamp, no secret, no match — all False.
        timestamp = (headers or {}).get("x-webhook-timestamp")
        verified = cast(Any, provider).verify_webhook(
            signature=signature, body=body, timestamp=timestamp
        )
    else:
        verified = provider.verify_webhook(signature=signature, body=body)
    if not verified:
        raise WebhookSignatureProblem("could not verify the webhook signature")

    try:
        parsed = provider.parse_webhook(body=body)
    except MalformedWebhook as exc:
        log.warning("payment.webhook.malformed", reason=str(exc))
        raise WebhookRejectedProblem("the webhook body could not be read") from None

    resolved_id = domain.resolve_event_id(header=event_id, event=parsed)
    if resolved_id is None:
        log.warning("payment.webhook.no_event_id", event_name=parsed.event_name)
        raise WebhookRejectedProblem("the webhook carries no usable event id")
    if event_id is not None and domain.usable_event_id(event_id) is None:
        # Present and unusable (blank, oversized, not printable ASCII): the body's own id or the
        # derived one took over. Worth a line — a proxy mangling the header would show up here.
        log.warning("payment.webhook.event_id_header_unusable", provider_event_id=resolved_id)
    event = dataclasses.replace(parsed, provider_event_id=resolved_id)
    redacted = _stored_payload(provider, event, headers)

    organization_id = _organization_of(event)
    if organization_id is None:
        _log_unattributable(event)
        return WebhookAck(
            received=True,
            provider_event_id=event.provider_event_id,
            duplicate=False,
            reconciled=False,
            ignored_reason=domain.REASON_UNATTRIBUTABLE,
        )

    # First statement in the transaction — the internal analogue of the tenancy chokepoint.
    await set_tenant(session, organization_id)
    if not is_production:
        await assert_tenant(session, organization_id)

    # Resolve the charge *before* the inbox insert, so the append-only `provider_events` row can
    # record `charge_id` at insert time — the table is insert-only, so it can never be back-filled.
    charge = await _resolve_charge(session, organization_id, event)
    # Read now, not after a rollback: a SAVEPOINT rollback expires the ORM row, and touching an
    # expired attribute in async code is an error rather than a reload.
    charge_id = charge.id if charge is not None else None

    try:
        async with session.begin_nested():
            recorded_id = await _insert_inbox(
                session,
                organization_id=organization_id,
                event=event,
                charge_id=charge_id,
                signature=signature,
                disposition=INBOX_PROCESSED,
                reason=None,
                payload=redacted,
            )
            if recorded_id is None:
                # The key is already in the inbox. Almost always a re-delivery — but the delivery
                # id is an *unsigned header*, so make sure the row that holds it is this event
                # and not a different signed body someone filed under a delivery id they knew.
                derived = domain.derive_event_id(event)
                stored = await selectors.get_provider_event(
                    session, organization_id, event.provider_event_id, provider=event.provider
                )
                if (
                    derived is not None
                    and stored is not None
                    and _identity_of(provider, stored.payload) not in (None, derived)
                ):
                    log.error(
                        "payment.webhook.event_id_reused",
                        provider_event_id=event.provider_event_id,
                        event_name=event.event_name,
                        organization_id=str(organization_id),
                    )
                    # Re-key *this* delivery on the id derived from its own signed body; every
                    # later delivery of it, under either id, is then a plain duplicate.
                    event = dataclasses.replace(event, provider_event_id=derived)
                    recorded_id = await _insert_inbox(
                        session,
                        organization_id=organization_id,
                        event=event,
                        charge_id=charge_id,
                        signature=signature,
                        disposition=INBOX_PROCESSED,
                        reason=None,
                        payload=redacted,
                    )
            if recorded_id is None:
                # Already in the inbox: a re-delivery. Idempotent — do nothing the first receipt
                # did not.
                return WebhookAck(
                    received=True,
                    provider_event_id=event.provider_event_id,
                    duplicate=True,
                    reconciled=False,
                )
            reconciled = await _reconcile_charge(session, charge, event, request=request)
    except domain.WebhookNotApplied as verdict:
        return await _record_not_applied(
            session,
            organization_id=organization_id,
            event=event,
            charge_id=charge_id,
            signature=signature,
            verdict=verdict,
            request=request,
            payload=redacted,
        )
    except IntegrityError as exc:
        if not _is_provider_id_collision(exc):
            raise
        return await _record_not_applied(
            session,
            organization_id=organization_id,
            event=event,
            charge_id=charge_id,
            signature=signature,
            verdict=domain.WebhookNotApplied(INBOX_CONFLICT, domain.REASON_PROVIDER_ID_CONFLICT),
            request=request,
            payload=redacted,
        )

    return WebhookAck(
        received=True,
        provider_event_id=event.provider_event_id,
        duplicate=False,
        reconciled=reconciled,
    )


async def _record_not_applied(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event: WebhookEvent,
    charge_id: uuid.UUID | None,
    signature: str | None,
    verdict: domain.WebhookNotApplied,
    request: Request | None,
    payee: str = PAYEE_PLATFORM,
    payload: dict[str, Any] | None = None,
) -> WebhookAck:
    """Keep an authentic webhook this platform cannot reconcile: the inbox row (stating why), an
    audit row, and a log line an operator can count — and answer 200.

    Runs after the SAVEPOINT rolled back, so nothing the failed attempt wrote survives, the state
    of the charge included. The row is written once more under the same key: if another delivery of
    this event got in first, that is a redelivery, and this one is a duplicate like any other.

    Nothing here carries a body or a person: the reason is a code, the audit `after` holds the
    event's name and id, the charge's id and state, and integer amounts and ISO currencies.
    """
    inbox_id = await _insert_inbox(
        session,
        organization_id=organization_id,
        event=event,
        charge_id=charge_id,
        signature=signature,
        disposition=verdict.disposition,
        reason=verdict.reason,
        payee=payee,
        payload=payload,
    )
    if inbox_id is None:
        return WebhookAck(
            received=True,
            provider_event_id=event.provider_event_id,
            duplicate=True,
            reconciled=False,
        )

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as=f"{event.provider}-webhook",
        action="payment.webhook.ignored",
        entity="provider_event",
        entity_id=inbox_id,
        after={
            "disposition": verdict.disposition,
            "reason": verdict.reason,
            "event_name": event.event_name,
            "provider_event_id": event.provider_event_id,
            "charge_id": str(charge_id) if charge_id is not None else None,
            **verdict.facts,
        },
        request=request,
    )
    # `conflict` means an authentic provider event contradicts our records and money may have moved:
    # error, so it pages. `ignored` is noise an operator can count: warning.
    emit = log.error if verdict.disposition == INBOX_CONFLICT else log.warning
    emit(
        "payment.webhook.ignored",
        disposition=verdict.disposition,
        reason=verdict.reason,
        provider_event_id=event.provider_event_id,
        event_name=event.event_name,
        organization_id=str(organization_id),
        charge_id=str(charge_id) if charge_id is not None else None,
    )
    return WebhookAck(
        received=True,
        provider_event_id=event.provider_event_id,
        duplicate=False,
        reconciled=False,
        ignored_reason=verdict.reason,
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
        charge = await selectors.lock_charge(session, organization_id, charge_id)
        if charge is not None:
            # A platform-signed webhook never reconciles a tenant's collection charge, whatever
            # id its notes carry: that charge's truth comes only from the merchant's own signed
            # callbacks (`app/collections/`), and moving it here would emit `commerce.payment.*`
            # and start subscription dunning for somebody's order (ADR-0024 §8).
            return charge if charge.payee == PAYEE_PLATFORM else None
    if event.provider_payment_id:
        found = await selectors.get_charge_by_provider_payment_id(
            session, organization_id, event.provider_payment_id
        )
        if found is not None:
            return await selectors.lock_charge(session, organization_id, found.id)
    return None


def _record_provider_ids(
    session: AsyncSession, charge: Charge, event: WebhookEvent, *, request: Request | None
) -> None:
    """Take the provider's payment and Order ids from a **verified** webhook when the charge has
    none yet. `finalize_charge` is not the only way a charge learns them: a provider call that
    outlived its timeout was abandoned, its result never recorded, but its capture webhook still
    arrives — and without this the charge would be captured with no provider id forever (no
    refund possible, a `captured` event naming nothing).

    Only ever *binds*. A payment id that differs from the one the charge already holds never
    reaches here: `_reconcile_charge` refuses it first (`provider_id_conflict`), because an event
    about another payment must not drive this charge's state."""
    if event.provider_payment_id and charge.provider_payment_id is None:
        charge.provider_payment_id = event.provider_payment_id
        # The first record of the provider's payment, however it arrives — the same single
        # `payment.charge.created` row `finalize_charge` writes when it gets there first.
        record_audit(
            session,
            AuditLog,
            organization_id=charge.organization_id,
            actor_id=None,
            acting_as=_actor(event),
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
    if event.provider_order_id and charge.provider_order_id is None:
        charge.provider_order_id = event.provider_order_id


def _capture_after_failure(
    session: AsyncSession,
    charge: Charge,
    event: WebhookEvent,
    event_type: str,
    *,
    variant: str,
    request: Request | None,
) -> None:
    """Record a capture for a charge already `failed` — the money moved, so the record follows it.

    `variant` is `local_failure` (the platform failed the charge on its own judgement:
    `attempts_exhausted`, `lookup_unavailable` — D3) or `provider_failure` (the provider itself
    reported the payment failed, and later reports **the same payment** captured: a bank's late
    authorisation). Each is audited under its own action and logged at error, because a person
    should know a charge dunning already acted on turned out to be paid. The charge's
    `commerce.payment.captured` is emitted exactly as a normal capture's — subscription's recovery
    is bound to every capture — and `failure_reason` is cleared.

    The log line never carries `charge.failure_reason` verbatim: for `local_failure` it is one of
    `domain.LOCAL_FAILURE_REASONS`, a stable code, safe to log as-is; for `provider_failure` it is
    Razorpay's free-text `error_description`, which does not belong in a structured line an
    operator greps and an alert matches on. The audit row keeps the full reason either way — that
    is the evidence trail, not the log."""
    log.error(
        f"payment.charge.captured_after_{variant}",
        charge_id=str(charge.id),
        organization_id=str(charge.organization_id),
        failure_reason=charge.failure_reason if variant == "local_failure" else None,
    )
    before_reason = charge.failure_reason
    charge.state = CHARGE_CAPTURED
    charge.failure_reason = None
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as=_actor(event),
        action=f"payment.charge.captured_after_{variant}",
        entity="charge",
        entity_id=charge.id,
        before={"state": CHARGE_FAILED, "failure_reason": before_reason},
        after={"state": charge.state, "provider_payment_id": charge.provider_payment_id},
        request=request,
    )
    _publish_payment_event(session, charge, event_type)


async def _detect_double_collection(
    session: AsyncSession, charge: Charge, event: WebhookEvent, *, request: Request | None
) -> None:
    """After `charge` just moved to `captured`, check whether the same invoice is *also* collected
    by another charge — the two things that can put a charge back into `captured` from `failed`
    (a dunning retry raising a fresh charge for the same invoice, and the original charge's own
    late same-payment capture) can both land, in either order, and neither on its own looks wrong:
    each is one charge going from `created`/`failed` to `captured`, exactly as an ordinary payment
    does. Only comparing across the invoice's charges catches it.

    Every capture runs this check — the normal path and both `_capture_after_failure` variants —
    so it fires whichever order the two captures arrive in. **No automatic refund**: an amount this
    small compared to the cost of refunding the wrong charge is an operator's call, made by hand,
    against the evidence this leaves (ADR-0004, PAY-A round 2 — owner decision). A charge with no
    `invoice_id` has nothing to compare against and is skipped.
    """
    if charge.invoice_id is None:
        return
    others = [
        other
        for other in await selectors.captured_charges_for_invoice(
            session, charge.organization_id, charge.invoice_id
        )
        if other.id != charge.id
    ]
    if not others:
        return
    other_ids = sorted(str(other.id) for other in others)
    log.error(
        "payment.invoice.collected_twice",
        organization_id=str(charge.organization_id),
        invoice_id=str(charge.invoice_id),
        charge_id=str(charge.id),
        other_captured_charge_ids=other_ids,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as=_actor(event),
        action="payment.invoice.collected_twice",
        entity="invoice",
        entity_id=charge.invoice_id,
        after={
            "invoice_id": str(charge.invoice_id),
            "other_captured_charge_ids": other_ids,
            "amount_minor": charge.amount_minor,
            "currency": charge.currency,
        },
        request=request,
    )


async def _reconcile_charge(
    session: AsyncSession,
    charge: Charge | None,
    event: WebhookEvent,
    *,
    request: Request | None,
) -> bool:
    """Move a charge to the outcome a webhook asserts and stage its `commerce.payment.*` event.

    Returns True only when a terminal outcome (`captured` / `failed`) actually moved the charge and
    emitted an event. An intermediate `authorized`, or a redelivered terminal already in that
    state, returns False — the inbox row is kept either way.

    **A permanent condition raises `domain.WebhookNotApplied`, never a `ProblemError`**: a charge
    that is not this organization's, a payment in another currency or for another amount than the
    charge, a payment id other than the one the charge holds, an outcome the state machine forbids.
    `ingest_webhook` runs this inside a SAVEPOINT and turns the verdict into a recorded, audited
    200 — an HTTP 4xx would only make the provider redeliver it for days. The identity checks come
    first, before anything is written.

    **`failed -> captured` is legal in two cases**, because in both the money moved: the platform
    failed the charge itself (`attempts_exhausted`, `lookup_unavailable`), or the provider reported
    the payment failed and now reports **the same payment** captured — a bank's late authorisation.
    Any other capture of a failed charge is an `illegal_transition` conflict.

    **Every capture also checks for a double collection** (`_detect_double_collection`): the same
    payment that failed and later captured can settle an invoice a dunning retry — a *different*
    charge for the same invoice — has already collected, in either order. Detected, not prevented,
    here: reconciliation records what a provider told it, and does not refuse or refund money that
    moved. `consumers.charge_on_dunning_retry` is the prevention, on the side that can still choose
    not to debit."""
    if charge is None:
        raise domain.unknown_charge()

    contradiction = domain.contradiction(
        charge_amount_minor=charge.amount_minor,
        charge_currency=charge.currency,
        event_amount_minor=event.amount_minor,
        event_currency=event.currency,
    )
    if contradiction is not None:
        raise contradiction

    if (
        event.provider_order_id
        and charge.provider_order_id
        and event.provider_order_id != charge.provider_order_id
    ):
        # A charge's payments are made against its own Order. An event naming another Order is not
        # about this charge, whatever `notes` it carries (a browser can stamp those): kept and
        # flagged, the state untouched.
        raise domain.order_id_conflict(charge.provider_order_id, event.provider_order_id)

    if charge.flow == FLOW_CHECKOUT and event.outcome_state != CHARGE_CAPTURED:
        # **Only a captured payment moves a checkout.** A customer pays an Order in checkout and may
        # retry it: a failed or abandoned attempt is not the charge failing (it would otherwise be
        # `commerce.payment.failed` — dunning, suspension — for a customer still mid-checkout), an
        # authorization is not yet money, and binding either attempt's id would turn the attempt
        # that finally succeeds into a `provider_id_conflict`. The checkout ends in `captured`, or
        # in `checkout_expired` (`expire_checkout_charge`), and in nothing else.
        if event.provider_order_id and charge.provider_order_id is None:
            charge.provider_order_id = event.provider_order_id
        await session.flush()
        return False

    # What the charge held *before* this event could bind anything: "the same payment" means the
    # payment the charge already carried, not the one this very event is about to write onto it.
    held_payment_id = charge.provider_payment_id
    if (
        event.provider_payment_id
        and held_payment_id
        and event.provider_payment_id != held_payment_id
    ):
        # An event about a different payment must not drive this charge (a second attempt on the
        # same Order, a stray id): kept and flagged, the state untouched.
        raise domain.provider_id_conflict(held_payment_id, event.provider_payment_id)

    _record_provider_ids(session, charge, event, request=request)
    target = event.outcome_state
    event_type = domain.event_for_outcome(target)

    if event_type is None:
        if target == CHARGE_AUTHORIZED and charge.state == CHARGE_FAILED:
            # Razorpay can report the same payment failed and then, on a bank's late
            # authorisation, authorized — with the capture to follow. Dunning has already acted on
            # the failure, so say so now, at error, before the capture arrives; no state change.
            log.error(
                "payment.webhook.late_authorization",
                charge_id=str(charge.id),
                organization_id=str(charge.organization_id),
                provider_event_id=event.provider_event_id,
            )
            record_audit(
                session,
                AuditLog,
                organization_id=charge.organization_id,
                actor_id=None,
                acting_as=_actor(event),
                action="payment.webhook.late_authorization",
                entity="charge",
                entity_id=charge.id,
                after={
                    "state": charge.state,
                    "provider_payment_id": charge.provider_payment_id,
                    "provider_event_id": event.provider_event_id,
                },
                request=request,
            )
        elif target == CHARGE_AUTHORIZED and domain.is_legal(charge.state, CHARGE_AUTHORIZED):
            # `created -> authorized`; nothing is emitted for an intermediate.
            charge.state = CHARGE_AUTHORIZED
        await session.flush()
        return False

    if charge.state == target:
        # A different event id asserting the same terminal outcome — idempotent no-op. Guarded
        # here so the state machine below never sees an illegal `failed -> failed` self-edge.
        await session.flush()
        return False

    if charge.state == CHARGE_FAILED and target == CHARGE_CAPTURED:
        variant: str | None = None
        if charge.failure_reason in domain.LOCAL_FAILURE_REASONS:
            variant = "local_failure"
        elif held_payment_id is not None and event.provider_payment_id == held_payment_id:
            variant = "provider_failure"
        if variant is not None:
            _capture_after_failure(
                session, charge, event, event_type, variant=variant, request=request
            )
            await session.flush()
            await _detect_double_collection(session, charge, event, request=request)
            return True

    if not domain.is_legal(charge.state, target):
        # `captured -> failed`, or `failed -> captured` that is neither a local failure nor the
        # same payment. Contradicts our record of the money: kept and flagged, not retried.
        raise domain.illegal_transition(charge.state, target)
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
        acting_as=_actor(event),
        action=f"payment.charge.{target}",
        entity="charge",
        entity_id=charge.id,
        before={"state": before},
        after={"state": charge.state},
        request=request,
    )
    _publish_payment_event(session, charge, event_type)
    if target == CHARGE_CAPTURED:
        await _detect_double_collection(session, charge, event, request=request)
    return True


async def apply_payment_snapshot(
    session: AsyncSession,
    charge: Charge,
    snapshot: PaymentSnapshot,
    *,
    request: Request | None = None,
    stray_order_id: str | None = None,
) -> bool:
    """Reconcile a **server-to-server status check** — what the provider says, now, about one
    payment on the charge's Order — through the very path a signed webhook takes
    (`_reconcile_charge`: the amount and currency checks, the Order check, the provider-id binding,
    the state machine, the audit row, the `commerce.payment.*` event, the double-collection check).
    Attributed `<provider>-sync`. The poller, a checkout's read-through and a confirm call this;
    **a customer's browser never does, and has no way to**: success is a captured payment found
    here, not a thing a client says.

    `stray_order_id` names the Order the payment was found on when it is **not** the one the charge
    records — a duplicate carrying the charge's exact receipt (`providers.base.StrayPayment`). Its
    identity is that receipt, which only this platform's own key can set on an Order and which the
    adapter matched exactly, server to server; so the Order check is skipped for it (and the
    recorded Order id is left alone). Everything else — the money, the payment id, the state
    machine — is checked as for any other snapshot.

    Returns whether a terminal outcome moved the charge. A status the neutral vocabulary asserts
    nothing from (`created`, an attempt in progress) is a no-op. A permanent condition — another
    amount, another Order, a payment that contradicts the record — is **kept and flagged, never
    raised**: it is audited (`payment.charge.sync_not_applied`) and logged at error, the state is
    untouched, and a person looks, exactly as for a webhook (`ingest_webhook`)."""
    outcome = domain.SNAPSHOT_OUTCOMES.get(snapshot.status)
    if outcome is None:
        return False
    # Read now: a SAVEPOINT rollback expires the ORM row, and touching an expired attribute in async
    # code is an error rather than a reload.
    charge_id = charge.id
    organization_id = charge.organization_id
    provider = charge.provider
    event = WebhookEvent(
        provider=provider,
        provider_event_id="",
        event_name=f"sync.payment.{snapshot.status}"[:80],
        outcome_state=outcome,
        organization_id=str(organization_id),
        charge_id=str(charge_id),
        provider_payment_id=snapshot.provider_payment_id,
        failure_reason=snapshot.error_code,
        provider_order_id=charge.provider_order_id if stray_order_id is None else None,
        entity_id=snapshot.provider_payment_id,
        amount_minor=snapshot.amount_minor,
        currency=snapshot.currency,
        source="sync",
    )
    verdict: domain.WebhookNotApplied | None = None
    try:
        async with session.begin_nested():
            return await _reconcile_charge(session, charge, event, request=request)
    except domain.WebhookNotApplied as caught:
        verdict = caught
    except IntegrityError as exc:
        if not _is_provider_id_collision(exc):
            raise
        verdict = domain.WebhookNotApplied(INBOX_CONFLICT, domain.REASON_PROVIDER_ID_CONFLICT)
    log.error(
        "payment.charge.sync_not_applied",
        charge_id=str(charge_id),
        organization_id=str(organization_id),
        provider=provider,
        disposition=verdict.disposition,
        reason=verdict.reason,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as=f"{provider}-sync",
        action="payment.charge.sync_not_applied",
        entity="charge",
        entity_id=charge_id,
        after={
            "disposition": verdict.disposition,
            "reason": verdict.reason,
            "provider_payment_id": snapshot.provider_payment_id,
            **({"stray_order_id": stray_order_id} if stray_order_id is not None else {}),
            **verdict.facts,
        },
        request=request,
    )
    return False


def refund_provider_key(refund_id: uuid.UUID) -> str:
    """The provider-side idempotency key of one refund: `rfd` and 36 hex characters of a digest of
    the refund's own id — 39 characters, alphanumeric only. Razorpay wants at least 10 characters
    of `[A-Za-z0-9_-]` (the old `refund:<uuid>` was 43 characters with a `:`, neither), and
    Cashfree's refund ids are 3-40 alphanumerics with no hyphen, so one shape serves both. Stable
    per refund, so a retried call is the same refund. (Tenant collections keep their own
    `rfd-<40 hex>`, Razorpay-only: `collections.domain.refund_idempotency_key`.)"""
    return f"rfd{hashlib.sha256(str(refund_id).encode()).hexdigest()[:36]}"


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
    if charge.payee != PAYEE_PLATFORM:
        raise BusinessRuleProblem("a collection charge is refunded through app/collections")
    if charge.state != CHARGE_CAPTURED:
        raise BusinessRuleProblem(
            f"only a captured charge can be refunded; this one is {charge.state!r}"
        )
    if charge.provider_payment_id is None:
        raise BusinessRuleProblem("cannot refund a charge with no provider payment id")
    if amount_minor <= 0:
        raise BusinessRuleProblem("a refund must be a positive amount")
    already_refunded = await selectors.refunded_total_for_charge(
        session, charge.organization_id, charge.id
    )
    if already_refunded + amount_minor > charge.amount_minor:
        raise BusinessRuleProblem(
            f"refunding {amount_minor} would exceed the charge: {already_refunded} of "
            f"{charge.amount_minor} minor units already refunded"
        )

    refund_id = uuid.uuid4()
    result = provider.refund(
        idempotency_key=refund_provider_key(refund_id),
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


async def require_charge(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> Charge:
    charge = await selectors.get_charge(session, organization_id, charge_id)
    if charge is None:
        # Invisible under RLS unless the transaction is bound to its organization, so another
        # tenant's id is a 404, never a disclosure.
        raise NotFoundProblem("No such charge")
    return charge
