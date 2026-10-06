"""Tenant collections, the pure part — no I/O, no session, no clock.

Five things are decided here:

1. **What a merchant's credentials must look like** (`mode_for_key_id`, `valid_secret`,
   `mask_secret`). Razorpay key ids carry their mode in the prefix (`rzp_test_` / `rzp_live_`),
   so the mode is derived, never asserted separately.
2. **What an observation of the provider does to a collection** (`decide`). A collection is a
   Razorpay *Order* the customer pays in Checkout, and an Order can take several payment attempts
   — a wrong OTP, then a successful retry. So unlike a platform charge, a collection may go
   `failed → captured` on a *different* payment of the same Order, and a late failure of an
   older attempt must never undo a capture. `cancelled` and `expired` are this platform's own
   decisions: a capture that lands after them is still recorded, because the money moved.
3. **Which provider words mean which state** (`COLLECTION_EVENT_OUTCOMES`,
   `state_for_provider_status`, `refund_state_for_provider_status`) — the only place collections
   knows Razorpay's vocabulary.
4. **What of a webhook body is kept** (`redact_webhook_body`). The inbox is append-only; a
   merchant webhook names the tenant's own customer (email, phone, card, UPI handle), and that
   must never be written somewhere it can never be erased.
5. **Whether this platform's record agrees with the provider's** (`reconcile_one`) — read-only,
   a list of stable mismatch codes, never a correction.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.domain import WebhookNotApplied, illegal_transition, provider_id_conflict
from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CANCELLED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_EXPIRED,
    CHARGE_FAILED,
    MERCHANT_MODE_LIVE,
    MERCHANT_MODE_TEST,
    REFUND_CREATED,
    REFUND_FAILED,
    REFUND_PROCESSED,
)
from app.providers.base import PaymentSnapshot
from app.providers.razorpay import redact_webhook_body

# --- what a collection is for ---------------------------------------------------------------

#: The one thing a collection is taken for today: an `operations` customer order (ADR-0024 §8).
REFERENCE_OPERATIONS_ORDER = "operations.order"
REFERENCE_TYPES: frozenset[str] = frozenset({REFERENCE_OPERATIONS_ORDER})

PROVIDER_RAZORPAY = "razorpay"
PROVIDERS: frozenset[str] = frozenset({PROVIDER_RAZORPAY})

# --- events (never `commerce.payment.*`: a merchant's failure must never suspend anyone) ------

EVT_COLLECTION_CAPTURED = "payment.collections.payment.captured"
EVT_COLLECTION_FAILED = "payment.collections.payment.failed"
EVT_COLLECTION_REFUNDED = "payment.collections.payment.refunded"

# --- credentials -----------------------------------------------------------------------------

_KEY_ID = re.compile(r"^rzp_(test|live)_[A-Za-z0-9]{6,40}$")
#: Printable ASCII, no whitespace, a sane length: what a Razorpay key secret or a webhook secret
#: a person chose looks like. Anything else is refused before it is ever sealed.
_SECRET = re.compile(r"^[\x21-\x7e]{8,128}$")
_MASK_BULLETS = "••••"


def mode_for_key_id(key_id: str) -> str | None:
    """`test` or `live` for a well-formed Razorpay key id, else None."""
    match = _KEY_ID.fullmatch(key_id)
    if match is None:
        return None
    return MERCHANT_MODE_TEST if match.group(1) == "test" else MERCHANT_MODE_LIVE


def valid_secret(value: str) -> bool:
    return _SECRET.fullmatch(value) is not None


def mask_secret(value: str) -> str:
    """A fixed run of bullets and the last four characters — enough for an admin to recognise
    which secret is configured, and never its length (`pii.mask`'s generic masker keeps one
    bullet per hidden character, which would)."""
    return _MASK_BULLETS + value[-4:]


def seal_aad(organization_id: object, account_id: object, field: str) -> bytes:
    """Associated data a merchant secret is sealed under: its organization, its row and which of
    the two secrets it is. A ciphertext lifted onto another row, or swapped between the two
    columns, then fails to open instead of authenticating somebody else's webhooks."""
    return f"payment.merchant_account:{organization_id}:{account_id}:{field}".encode()


def refund_idempotency_key(organization_id: object, charge_id: object, caller_key: str) -> str:
    """The provider's refund idempotency key for a caller's key: stable (a retry is the same
    refund at Razorpay), scoped to one charge (the same caller key on two charges is two
    refunds), and inside Razorpay's own shape — at least 10 of `[A-Za-z0-9_-]`."""
    digest = hashlib.sha256(f"{organization_id}:{charge_id}:{caller_key}".encode()).hexdigest()
    return f"rfd-{digest[:40]}"


# --- provider vocabulary ---------------------------------------------------------------------

#: Razorpay event name → the collection outcome it asserts. `order.paid` carries the payment
#: entity too, and means exactly what `payment.captured` does for an Order-based payment.
COLLECTION_EVENT_OUTCOMES: dict[str, str] = {
    "payment.authorized": CHARGE_AUTHORIZED,
    "payment.captured": CHARGE_CAPTURED,
    "payment.failed": CHARGE_FAILED,
    "order.paid": CHARGE_CAPTURED,
}
#: Razorpay refund events. Their `payload.refund.entity` is the refund; `payload.payment.entity`
#: the payment it returns money from.
REFUND_EVENTS: frozenset[str] = frozenset({"refund.created", "refund.processed", "refund.failed"})

_PAYMENT_STATUS_STATES: dict[str, str] = {
    "created": CHARGE_CREATED,
    "authorized": CHARGE_AUTHORIZED,
    "captured": CHARGE_CAPTURED,
    # A captured payment whose money has since gone back: the collection stays captured and the
    # refund is its own record.
    "refunded": CHARGE_CAPTURED,
    "failed": CHARGE_FAILED,
}
_REFUND_STATUS_STATES: dict[str, str] = {
    "pending": REFUND_CREATED,
    "created": REFUND_CREATED,
    "processed": REFUND_PROCESSED,
    "failed": REFUND_FAILED,
}


def state_for_provider_status(status: str) -> str | None:
    return _PAYMENT_STATUS_STATES.get(status)


def refund_state_for_provider_status(status: str | None) -> str:
    """A refund whose status the provider did not say (or said in words this code does not
    know) is `created` — pending, the one state that claims nothing about the outcome."""
    return _REFUND_STATUS_STATES.get(status or "", REFUND_CREATED)


# --- the collection state machine ------------------------------------------------------------

#: A collection that can still be paid, or retried, on its provider Order.
OPEN_STATES: frozenset[str] = frozenset({CHARGE_CREATED, CHARGE_AUTHORIZED, CHARGE_FAILED})
#: This platform's own decision to stop waiting — never the provider's word about the money.
LOCALLY_CLOSED_STATES: frozenset[str] = frozenset({CHARGE_CANCELLED, CHARGE_EXPIRED})
#: The states an operator may cancel from: nothing is authorised or captured.
CANCELLABLE_STATES: frozenset[str] = frozenset({CHARGE_CREATED, CHARGE_FAILED})

REASON_CANCELLED_BY_OPERATOR = "cancelled_by_operator"
REASON_CHECKOUT_EXPIRED = "checkout_expired"
REASON_UNHANDLED_EVENT = "unhandled_event"
REASON_UNKNOWN_REFUND = "unknown_refund"


@dataclass(frozen=True, slots=True)
class Observation:
    """What the provider says happened to one payment attempt on a collection's Order — from a
    signature-verified webhook or a server-to-server status check, never from a browser."""

    outcome: str
    provider_payment_id: str | None
    provider_order_id: str | None
    amount_minor: int | None
    currency: str | None
    #: The provider's human description of a failure (kept on the charge, truncated; never
    #: logged) and its stable code (kept in the status history).
    failure_reason: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """What an observation does to a collection. `to_state` None is no state change;
    `bind_payment` writes the observation's payment id onto the charge; `emit` is the event to
    stage; `note` names an unusual path for the log and the audit trail."""

    to_state: str | None = None
    bind_payment: bool = False
    emit: str | None = None
    note: str | None = None
    failure_reason: str | None = None
    clear_failure: bool = False

    @property
    def changes_anything(self) -> bool:
        return self.to_state is not None or self.bind_payment


NOOP = Decision()


def decide(state: str, held_payment_id: str | None, observation: Observation) -> Decision:
    """The collection state machine. Raises `WebhookNotApplied` for an observation that
    contradicts what is recorded (the caller keeps it, flags it, and changes nothing).

    * **captured** — from any open state, or from `cancelled`/`expired` (the money moved after
      this platform stopped waiting: recorded, flagged `captured_after_*`). A second, different
      payment captured on an Order already captured is impossible at Razorpay (a paid Order
      takes no more payments), so it is a conflict, never a quiet overwrite.
    * **authorized** — from `created`/`failed`; after `cancelled`/`expired` it is noted
      (`late_authorization`) and the capture that follows does the moving.
    * **failed** — from `created`, or from `authorized` for the *same* payment. Another attempt
      failing while one is authorized, or an older attempt's failure arriving after the capture,
      changes nothing. The same payment failing after it was captured is a conflict.
    """
    outcome = observation.outcome
    payment_id = observation.provider_payment_id
    other_payment = bool(payment_id and held_payment_id and payment_id != held_payment_id)

    if outcome == CHARGE_CAPTURED:
        if state == CHARGE_CAPTURED:
            if other_payment:
                assert held_payment_id is not None and payment_id is not None
                raise provider_id_conflict(held_payment_id, payment_id)
            return NOOP
        note = f"captured_after_{state}" if state in LOCALLY_CLOSED_STATES else None
        return Decision(
            to_state=CHARGE_CAPTURED,
            bind_payment=bool(payment_id),
            emit=EVT_COLLECTION_CAPTURED,
            note=note,
            clear_failure=True,
        )

    if outcome == CHARGE_AUTHORIZED:
        if state in (CHARGE_CREATED, CHARGE_FAILED):
            return Decision(
                to_state=CHARGE_AUTHORIZED, bind_payment=bool(payment_id), clear_failure=True
            )
        if state in LOCALLY_CLOSED_STATES:
            return Decision(note="late_authorization")
        return NOOP

    if outcome == CHARGE_FAILED:
        reason = (observation.failure_reason or "")[:200] or None
        if state == CHARGE_CREATED or (state == CHARGE_AUTHORIZED and not other_payment):
            return Decision(
                to_state=CHARGE_FAILED,
                bind_payment=bool(payment_id),
                emit=EVT_COLLECTION_FAILED,
                failure_reason=reason,
            )
        if state == CHARGE_FAILED and other_payment:
            # Another attempt on the same Order failed too: remember the latest, move nothing.
            return Decision(bind_payment=True, failure_reason=reason)
        if state == CHARGE_CAPTURED and not other_payment and payment_id is not None:
            raise illegal_transition(CHARGE_CAPTURED, CHARGE_FAILED)
        return NOOP

    return NOOP


def observation_from_snapshot(snapshot: PaymentSnapshot, order_id: str) -> Observation | None:
    """A server-to-server status check's answer as an `Observation`. None for a status this
    code does not know — which then moves nothing."""
    state = state_for_provider_status(snapshot.status)
    if state is None or state == CHARGE_CREATED:
        return None
    return Observation(
        outcome=state,
        provider_payment_id=snapshot.provider_payment_id,
        provider_order_id=order_id,
        amount_minor=snapshot.amount_minor,
        currency=snapshot.currency,
        failure_reason=snapshot.error_description or snapshot.error_code,
        failure_code=snapshot.error_code,
    )


def payment_error_code(parsed: dict[str, Any]) -> str | None:
    """The stable `error_code` of a webhook's payment entity (a free-text description is never
    what the history records)."""
    payload = parsed.get("payload")
    wrapper = payload.get("payment") if isinstance(payload, dict) else None
    entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
    code = entity.get("error_code") if isinstance(entity, dict) else None
    return code[:64] if isinstance(code, str) and code else None


# --- webhook body redaction ------------------------------------------------------------------
# The allow-list itself lives with the adapter that knows Razorpay's payload shape
# (`app.providers.razorpay.redact_webhook_body`): platform billing's inbox needs the very same
# reduction since Phase A, and the two must never drift. Re-exported here, where collections
# has always found it (see `__all__`).


@dataclass(frozen=True, slots=True)
class RefundFacts:
    """A refund entity from a verified refund webhook — ids, money and status only."""

    provider_refund_id: str
    provider_payment_id: str | None
    amount_minor: int | None
    currency: str | None
    status: str | None


def refund_facts(parsed: dict[str, Any]) -> RefundFacts | None:
    payload = parsed.get("payload")
    wrapper = payload.get("refund") if isinstance(payload, dict) else None
    entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
    if not isinstance(entity, dict):
        return None
    refund_id = entity.get("id")
    if not isinstance(refund_id, str) or not refund_id:
        return None

    def _text(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    amount = entity.get("amount")
    return RefundFacts(
        provider_refund_id=refund_id,
        provider_payment_id=_text(entity.get("payment_id")),
        amount_minor=amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
        currency=(_text(entity.get("currency")) or "").upper() or None,
        status=_text(entity.get("status")),
    )


# --- reconciliation (read-only) --------------------------------------------------------------

MISMATCH_CAPTURED_AT_PROVIDER_ONLY = "captured_at_provider_only"
MISMATCH_NOT_CAPTURED_AT_PROVIDER = "not_captured_at_provider"
MISMATCH_PAYMENT_ID = "payment_id_mismatch"
MISMATCH_AMOUNT = "amount_mismatch"
MISMATCH_CURRENCY = "currency_mismatch"
MISMATCH_REFUNDED = "refund_mismatch"


def reconcile_one(
    *,
    state: str,
    amount_minor: int,
    currency: str,
    provider_payment_id: str | None,
    refunded_minor: int,
    provider_payments: list[PaymentSnapshot],
) -> list[str]:
    """Every way this platform's record of one collection disagrees with the provider's answer,
    as stable codes. Empty when they agree. Pure, and never a correction: the report shows it,
    a person decides (and a status check or webhook, not this, is what moves state)."""
    captured = [p for p in provider_payments if p.status in ("captured", "refunded")]
    mismatches: list[str] = []
    if state == CHARGE_CAPTURED and not captured:
        return [MISMATCH_NOT_CAPTURED_AT_PROVIDER]
    if state != CHARGE_CAPTURED:
        return [MISMATCH_CAPTURED_AT_PROVIDER_ONLY] if captured else []
    paid = next((p for p in captured if p.provider_payment_id == provider_payment_id), None)
    if paid is None:
        mismatches.append(MISMATCH_PAYMENT_ID)
        paid = captured[0]
    if paid.currency.upper() != currency.upper():
        mismatches.append(MISMATCH_CURRENCY)
    elif paid.amount_minor != amount_minor:
        mismatches.append(MISMATCH_AMOUNT)
    if paid.amount_refunded_minor != refunded_minor:
        mismatches.append(MISMATCH_REFUNDED)
    return mismatches


__all__ = [
    "COLLECTION_EVENT_OUTCOMES",
    "EVT_COLLECTION_CAPTURED",
    "EVT_COLLECTION_FAILED",
    "EVT_COLLECTION_REFUNDED",
    "NOOP",
    "REFERENCE_OPERATIONS_ORDER",
    "Decision",
    "Observation",
    "WebhookNotApplied",
    "decide",
    "mask_secret",
    "mode_for_key_id",
    "reconcile_one",
    "redact_webhook_body",
]
