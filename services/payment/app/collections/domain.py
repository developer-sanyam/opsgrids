"""Tenant collections, the pure part — no I/O, no session, no clock.

Five things are decided here:

1. **What a merchant's credentials must look like, per provider** (`ProviderSpec`,
   `validate_credentials`, `mask_secret`). Razorpay key ids carry their mode in the prefix
   (`rzp_test_` / `rzp_live_`), so the mode is derived, never asserted separately; a Cashfree App ID
   carries none, so the merchant chooses Sandbox or Production and only an obvious mismatch with
   the key's shape is refused.
2. **What an observation of the provider does to a collection** (`decide`). A collection is a
   provider *Order* the customer pays in the provider's checkout, and an Order can take several
   payment attempts — a wrong OTP, then a successful retry. So unlike a platform charge, a
   collection may go `failed → captured` on a *different* payment of the same Order, and a late
   failure of an older attempt must never undo a capture. `cancelled` and `expired` are this
   platform's own decisions: a capture that lands after them is still recorded, because the money
   moved.
3. **Which neutral payment status means which state** (`state_for_provider_status`). A provider's
   own words — event names, refund statuses, where an error code sits, what of a webhook body is
   kept — live with its adapter (ADR-0033: `app/providers/razorpay_collections.py`,
   `app/providers/cashfree_collections.py`), which hands collections `CollectionWebhookFacts` in
   the platform's vocabulary.
4. **What a refund's provider key is** (`refund_idempotency_key`), in each provider's own shape.
5. **Whether this platform's record agrees with the provider's** (`reconcile_one`) — read-only,
   a list of stable mismatch codes, never a correction.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

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
)
from app.providers import cashfree_codec
from app.providers import cashfree_collections as cashfree
from app.providers import razorpay_collections as razorpay
from app.providers.base import PaymentSnapshot, valid_phone

# Re-exported where collections has always found it: Razorpay's webhook allow-list
# (`app.providers.razorpay.redact_webhook_body`, shared with the platform inbox).
from app.providers.razorpay import redact_webhook_body

# --- what a collection is for ---------------------------------------------------------------

#: The one thing a collection is taken for today: an `operations` customer order (ADR-0024 §8).
REFERENCE_OPERATIONS_ORDER = "operations.order"
REFERENCE_TYPES: frozenset[str] = frozenset({REFERENCE_OPERATIONS_ORDER})

PROVIDER_RAZORPAY = "razorpay"
PROVIDER_CASHFREE = "cashfree"
PROVIDERS: frozenset[str] = frozenset({PROVIDER_RAZORPAY, PROVIDER_CASHFREE})

# --- the merchant webhook --------------------------------------------------------------------

#: The neutral public path every merchant webhook is delivered to (`?account=<id>` names the
#: account, and the account row — never the URL or the body — names the provider).
WEBHOOK_PATH = "/api/v1/webhooks/collections"
#: The path Razorpay merchants registered before there was a second provider. Kept forever as an
#: alias onto the same handler: a merchant's dashboard is not ours to edit.
LEGACY_RAZORPAY_WEBHOOK_PATH = "/api/v1/webhooks/razorpay/collections"
#: Cashfree's ceiling on `order_meta.notify_url`.
NOTIFY_URL_MAX = 250


def webhook_url(public_api_base: str, account_id: object, path: str = WEBHOOK_PATH) -> str:
    """The absolute URL a merchant registers (and a Cashfree Order is told to notify): the
    platform's public `/api/v1` base, the webhook path, and the account."""
    return f"{public_api_base.rstrip('/')}{path.removeprefix('/api/v1')}?account={account_id}"


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """What collections must know about a provider **before** an adapter is built — in a claim's
    transaction, from the account row alone. Everything else about the provider is its adapter's.
    """

    name: str
    #: How a person is told the provider's name ("Cashfree").
    display_name: str
    #: Razorpay signs deliveries with a webhook secret the merchant chooses; Cashfree signs them
    #: with the API Secret Key, so a Cashfree account holds one secret, not two.
    has_webhook_secret: bool
    #: Cashfree will not create an Order without the paying customer (an id and a phone).
    requires_payer: bool
    #: The browser's handle is a short-lived token fetched from the provider every time a checkout
    #: is (re)opened (Cashfree's `payment_session_id`, never stored) rather than the Order id the
    #: platform keeps (Razorpay).
    reopen_asks_provider: bool
    #: The provider Order is created with the checkout's own expiry and takes payment until then
    #: (Cashfree's `order_expiry_time`), whatever this platform decides meanwhile. A Razorpay Order
    #: has no expiry; it can only be paid through a Checkout this platform opens.
    order_outlives_cancel: bool
    #: What the merchant subscribes to on the webhook it registers.
    webhook_events: tuple[str, ...]
    #: The provider's refund key for a refund whose identity hashes to a hex digest.
    refund_key: Callable[[str], str]
    #: The (lower-cased) header a delivery's signature travels in — kept on the inbox row.
    signature_header: str
    #: The path a merchant is shown to register. Both paths reach one handler, which takes the
    #: provider from the account row; a Razorpay account keeps showing the path every Razorpay
    #: merchant already registered.
    webhook_path: str


RAZORPAY = ProviderSpec(
    name=PROVIDER_RAZORPAY,
    display_name="Razorpay",
    has_webhook_secret=True,
    requires_payer=False,
    reopen_asks_provider=False,
    order_outlives_cancel=False,
    webhook_events=razorpay.WEBHOOK_EVENTS,
    refund_key=razorpay.refund_key,
    signature_header=razorpay.SIGNATURE_HEADER,
    webhook_path=LEGACY_RAZORPAY_WEBHOOK_PATH,
)
CASHFREE = ProviderSpec(
    name=PROVIDER_CASHFREE,
    display_name="Cashfree",
    has_webhook_secret=False,
    requires_payer=True,
    reopen_asks_provider=True,
    order_outlives_cancel=True,
    webhook_events=cashfree.WEBHOOK_EVENTS,
    refund_key=cashfree.refund_key,
    signature_header=cashfree.SIGNATURE_HEADER,
    webhook_path=WEBHOOK_PATH,
)
_SPECS: dict[str, ProviderSpec] = {RAZORPAY.name: RAZORPAY, CASHFREE.name: CASHFREE}


def spec_for(provider: str) -> ProviderSpec:
    """The spec of a provider an account row names. A row naming anything else is a defect (the
    database CHECK refuses one), raised rather than guessed at."""
    try:
        return _SPECS[provider]
    except KeyError:
        raise ValueError(f"{provider!r} is not a collections provider") from None


# --- events (never `commerce.payment.*`: a merchant's failure must never suspend anyone) ------

EVT_COLLECTION_CAPTURED = "payment.collections.payment.captured"
EVT_COLLECTION_FAILED = "payment.collections.payment.failed"
EVT_COLLECTION_REFUNDED = "payment.collections.payment.refunded"

# --- credentials -----------------------------------------------------------------------------

_KEY_ID = re.compile(r"^rzp_(test|live)_[A-Za-z0-9]{6,40}$")
#: Printable ASCII, no whitespace, a sane length: what a Razorpay key secret, a webhook secret a
#: person chose or a Cashfree Secret Key looks like. Anything else is refused before it is sealed.
_SECRET = re.compile(r"^[\x21-\x7e]{8,128}$")
#: A Cashfree App ID: a sanity check only (no prefix rule — Cashfree documents none), letters,
#: digits, `_` and `-`.
_APP_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_MASK_BULLETS = "••••"

#: The environments a merchant chooses for a Cashfree account, and the platform mode each is.
CASHFREE_MODES: dict[str, str] = {"sandbox": MERCHANT_MODE_TEST, "production": MERCHANT_MODE_LIVE}


def mode_for_key_id(key_id: str) -> str | None:
    """`test` or `live` for a well-formed Razorpay key id, else None."""
    match = _KEY_ID.fullmatch(key_id)
    if match is None:
        return None
    return MERCHANT_MODE_TEST if match.group(1) == "test" else MERCHANT_MODE_LIVE


def valid_secret(value: str) -> bool:
    return _SECRET.fullmatch(value) is not None


class CredentialProblem(ValueError):
    """A merchant's credentials are not what the provider's would look like, or are not allowed
    here. The message names the field and never echoes a secret."""


def validate_credentials(
    provider: str,
    *,
    key_id: str,
    key_secret: str,
    webhook_secret: str | None,
    mode: str | None,
    allow_live: bool,
) -> str:
    """The account's mode (`test` / `live`), or a `CredentialProblem` naming what is wrong.

    * **Razorpay** — the key id's prefix is the mode (`mode` must not be given: it cannot
      disagree with the key), a webhook secret is required, and live keys need `allow_live`.
    * **Cashfree** — the merchant chooses `sandbox` or `production` (an App ID does not say), the
      Secret Key signs webhooks so no webhook secret is accepted, Production needs `allow_live`,
      and keys whose shape plainly belongs to the other environment are refused (a safe
      false-refuse, never a reason to let a key through)."""
    if provider not in PROVIDERS:
        raise CredentialProblem(f"{provider!r} is not a supported payment provider")
    if not valid_secret(key_secret):
        raise CredentialProblem("key_secret must be 8-128 printable characters with no spaces")
    if provider == PROVIDER_RAZORPAY:
        derived = mode_for_key_id(key_id)
        if derived is None:
            raise CredentialProblem("key_id must be a Razorpay key id (rzp_test_… or rzp_live_…)")
        if mode is not None:
            raise CredentialProblem("mode is not set for Razorpay: the key id says it")
        if derived == MERCHANT_MODE_LIVE and not allow_live:
            raise CredentialProblem(
                "Live Razorpay keys are not enabled on this platform yet; connect test-mode keys "
                "(rzp_test_…)"
            )
        if webhook_secret is None or not valid_secret(webhook_secret):
            raise CredentialProblem(
                "webhook_secret must be 8-128 printable characters with no spaces"
            )
        return derived
    if not _APP_ID.fullmatch(key_id):
        raise CredentialProblem(
            "key_id must be a Cashfree App ID: 4-64 letters, digits, '_' or '-'"
        )
    if webhook_secret is not None:
        raise CredentialProblem(
            "webhook_secret is not set for Cashfree: its Secret Key signs every webhook"
        )
    if mode not in CASHFREE_MODES:
        raise CredentialProblem("mode must be sandbox or production for Cashfree")
    platform_mode = CASHFREE_MODES[mode]
    if platform_mode == MERCHANT_MODE_LIVE and not allow_live:
        raise CredentialProblem(
            "Cashfree Production is not enabled on this platform yet; connect a Sandbox account"
        )
    if mode == "sandbox" and cashfree_codec.production_shaped(key_secret):
        raise CredentialProblem(
            "These look like Production keys; choose Production, or use the Sandbox keys"
        )
    if mode == "production" and cashfree_codec.sandbox_shaped(key_id, key_secret):
        raise CredentialProblem(
            "These look like Sandbox keys; choose Sandbox, or use the Production keys"
        )
    return platform_mode


def checkout_mode(mode: str) -> str:
    """A Cashfree account's platform mode as its SDK names it (`Cashfree({mode})`)."""
    return "production" if mode == MERCHANT_MODE_LIVE else "sandbox"


#: Characters an operator types between a phone's digits that are not part of the number.
_PHONE_SEPARATORS = re.compile(r"[\s().-]")


def normalize_phone(raw: str | None) -> str | None:
    """A customer phone as a provider takes it — 10 digits, or `+` and 8-15 digits — with the
    spaces, dots, dashes and brackets a person types removed; None when what is left is not one.
    Nothing is ever added: a phone is never invented, completed or guessed."""
    if not raw:
        return None
    candidate = _PHONE_SEPARATORS.sub("", raw.strip())
    return candidate if valid_phone(candidate) else None


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


def refund_idempotency_key(
    organization_id: object, charge_id: object, caller_key: str, provider: str = PROVIDER_RAZORPAY
) -> str:
    """The provider's refund key for a caller's key: stable (a retry is the same refund at the
    provider), scoped to one charge (the same caller key on two charges is two refunds), and in the
    provider's own shape — Razorpay's `X-Refund-Idempotency` (`rfd-` and 40 hex, unchanged) or
    Cashfree's `refund_id` (`rfd` and 36 hex: 3-40 alphanumeric). Also what the refund row is found
    again by (`refunds.idempotency_key`)."""
    digest = hashlib.sha256(f"{organization_id}:{charge_id}:{caller_key}".encode()).hexdigest()
    return spec_for(provider).refund_key(digest)


# --- the neutral payment vocabulary ----------------------------------------------------------

#: `PaymentSnapshot.status` is in the neutral vocabulary every adapter maps its provider's own
#: statuses into (`app/providers/base.py`); this is what each means for a collection.
_PAYMENT_STATUS_STATES: dict[str, str] = {
    "created": CHARGE_CREATED,
    "authorized": CHARGE_AUTHORIZED,
    "captured": CHARGE_CAPTURED,
    # A captured payment whose money has since gone back: the collection stays captured and the
    # refund is its own record.
    "refunded": CHARGE_CAPTURED,
    "failed": CHARGE_FAILED,
}


def state_for_provider_status(status: str) -> str | None:
    return _PAYMENT_STATUS_STATES.get(status)


# --- the collection state machine ------------------------------------------------------------

#: A collection that can still be paid, or retried, on its provider Order.
OPEN_STATES: frozenset[str] = frozenset({CHARGE_CREATED, CHARGE_AUTHORIZED, CHARGE_FAILED})
#: This platform's own decision to stop waiting — never the provider's word about the money.
LOCALLY_CLOSED_STATES: frozenset[str] = frozenset({CHARGE_CANCELLED, CHARGE_EXPIRED})
#: The states an operator may cancel from: nothing is authorised or captured.
CANCELLABLE_STATES: frozenset[str] = frozenset({CHARGE_CREATED, CHARGE_FAILED})

#: How long after this platform closes a checkout (cancels or expires it) a payment the customer
#: had already started can still complete at the provider — a UPI collect request approved late on
#: the customer's phone. Also no shorter than the least lifetime a Cashfree Order is (re)created
#: with (`services._MIN_ORDER_LIFETIME`), so an Order made just before the close is covered.
LATE_PAYMENT_GRACE = timedelta(minutes=20)


def payable_until(
    spec: ProviderSpec, *, created_at: datetime, closed_at: datetime, checkout_ttl: timedelta
) -> datetime:
    """Until when a collection this platform has closed (`cancelled`, `expired`) may still be paid
    at its provider: the late-payment grace after the close, and — for a provider whose Order
    keeps its own expiry (Cashfree) — the Order's lifetime from the collection's creation. Closing
    a checkout here does not close the provider's Order; until this moment a payment can still
    land, and only a connected account can verify the webhook that says so."""
    until = closed_at + LATE_PAYMENT_GRACE
    if spec.order_outlives_cancel:
        until = max(until, created_at + checkout_ttl)
    return until


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
    "CASHFREE",
    "EVT_COLLECTION_CAPTURED",
    "EVT_COLLECTION_FAILED",
    "EVT_COLLECTION_REFUNDED",
    "NOOP",
    "RAZORPAY",
    "REFERENCE_OPERATIONS_ORDER",
    "CredentialProblem",
    "Decision",
    "Observation",
    "ProviderSpec",
    "WebhookNotApplied",
    "decide",
    "mask_secret",
    "mode_for_key_id",
    "normalize_phone",
    "reconcile_one",
    "redact_webhook_body",
    "spec_for",
    "validate_credentials",
]
