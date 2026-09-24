"""The charge state machine and the reconciliation mapping. Pure — no I/O, no session, no clock.

Three things are decided here and nothing else:

1. **Is a charge move legal?** A charge is `created`, becomes `authorized`, and reaches a terminal
   `captured` or `failed`. `captured -> failed` is not a thing that can happen — a captured
   payment is money in the account — and pretending otherwise is how the subscription downstream
   comes to disagree with what was actually collected. The allowed edges are a small closed table.

2. **What does a reconciled outcome emit?** A webhook that captures a charge emits
   `commerce.payment.captured`; one that fails it emits `commerce.payment.failed`, and *that* is
   the event the subscription service turns into a suspension, a de-entitled activation, and a 402
   (ADR-0004 §5). An `authorized` intermediate emits nothing — it is not yet a fact anyone
   downstream acts on.

3. **What identifies a webhook, and can it be reconciled?** The inbox is idempotent on an event
   id that is never empty (`resolve_event_id`), and a verified webhook that contradicts what this
   platform holds is a *permanent* condition, named by a stable reason code and raised as
   `WebhookNotApplied` for the service to record — never as a retryable HTTP error.

Kept pure so it is trivially testable and cannot grow a side effect: `services.py` is where the
row, the audit entry and the outbox event are staged, all in one transaction.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any

from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_FAILED,
    INBOX_CONFLICT,
    INBOX_IGNORED,
)
from app.providers.base import WebhookEvent

EVT_PREFIX = "commerce.payment."
EVT_CAPTURED = EVT_PREFIX + "captured"
EVT_FAILED = EVT_PREFIX + "failed"

#: The legal charge edges. Terminal states map to nothing, which is what makes `captured -> failed`
#: and `failed -> captured` refused. A self-edge on a terminal state is *tolerated* as a no-op by
#: the reconciler (a redelivered webhook), handled there rather than here.
ALLOWED: dict[str, frozenset[str]] = {
    CHARGE_CREATED: frozenset({CHARGE_AUTHORIZED, CHARGE_CAPTURED, CHARGE_FAILED}),
    CHARGE_AUTHORIZED: frozenset({CHARGE_CAPTURED, CHARGE_FAILED}),
    CHARGE_CAPTURED: frozenset(),
    CHARGE_FAILED: frozenset(),
}

#: The terminal outcome states a reconciliation may assert, and the commerce event each emits.
#: `authorized` is deliberately absent: it is an intermediate the platform does not broadcast.
_OUTCOME_EVENT: dict[str, str] = {
    CHARGE_CAPTURED: EVT_CAPTURED,
    CHARGE_FAILED: EVT_FAILED,
}


def is_legal(from_state: str, to_state: str) -> bool:
    """Whether the state machine allows the move. There is deliberately no `assert_legal` that
    raises an HTTP error: the only caller that refuses a move is webhook reconciliation, and an
    illegal move asserted by a *signed* webhook is a permanent condition to record
    (`illegal_transition`), not a 422 the provider would redeliver for days."""
    return to_state in ALLOWED.get(from_state, frozenset())


def event_for_outcome(outcome_state: str) -> str | None:
    """The `commerce.payment.*` type a reconciled outcome emits, or None for an intermediate."""
    return _OUTCOME_EVENT.get(outcome_state)


def is_terminal(state: str) -> bool:
    return state in (CHARGE_CAPTURED, CHARGE_FAILED)


#: `charges.failure_reason` codes for a failure this platform decided, not the provider. A charge
#: failed for one of these may still be captured by a late webhook — the provider may hold a
#: payment the platform never saw confirmed — so reconciliation accepts `failed -> captured` for
#: exactly these (`services._reconcile_charge`) and nothing else (D3).
FAILURE_ATTEMPTS_EXHAUSTED = "attempts_exhausted"
FAILURE_LOOKUP_UNAVAILABLE = "lookup_unavailable"
LOCAL_FAILURE_REASONS: frozenset[str] = frozenset(
    {FAILURE_ATTEMPTS_EXHAUSTED, FAILURE_LOOKUP_UNAVAILABLE}
)


#: The first wait between two provider calls for one stuck charge, doubling per attempt to
#: `CHARGE_RETRY_CEILING`. Pure arithmetic — the sweep and the lease claim both read it (D3).
CHARGE_RETRY_BASE: timedelta = timedelta(minutes=5)
CHARGE_RETRY_CEILING: timedelta = timedelta(hours=1)


def charge_retry_backoff(attempts: int) -> timedelta:
    """How long after claiming call number `attempts` (1-based) the next one may be claimed.
    Exponential, capped, so a provider that is down for an hour is not hammered by a sweep that
    ticks every minute, and a charge that can never succeed reaches its attempt budget in hours,
    not days."""
    attempts = max(1, attempts)
    wait: timedelta = CHARGE_RETRY_BASE * (2 ** (attempts - 1))
    return min(wait, CHARGE_RETRY_CEILING)


# --- webhook identity -------------------------------------------------------------------

#: `provider_events.provider_event_id` is `String(120)`.
EVENT_ID_MAX_LENGTH = 120
#: Printable ASCII, no whitespace — what a provider's event id is in practice, and safe to store,
#: log and compare. Anything else is treated as "no id" and the next source is tried.
_EVENT_ID_SHAPE = re.compile(r"[\x21-\x7e]+")
#: Prefix of an id this platform derived, never one a provider or a header can supply: the two
#: namespaces are disjoint by construction, so a header cannot pose as a derived key.
DERIVED_EVENT_ID_PREFIX = "derived:"


def usable_event_id(value: str | None) -> str | None:
    """`value` as an inbox key, or None if it cannot be one: absent, blank, too long for the
    column, not printable ASCII, or claiming the derived namespace. **Never the empty string.**"""
    if value is None:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > EVENT_ID_MAX_LENGTH:
        return None
    if _EVENT_ID_SHAPE.fullmatch(candidate) is None:
        return None
    if candidate.startswith(DERIVED_EVENT_ID_PREFIX):
        return None
    return candidate


def derive_event_id(event: WebhookEvent) -> str | None:
    """A stable id for a delivery that carries none: a digest of the body's own event name, the
    entity it is about and the provider's timestamp — the same on every redelivery of the same
    event, different for a different event. None unless all three are present: an id derived from
    less would fold distinct events together, which is the failure this exists to prevent."""
    if not event.event_name or not event.entity_id or event.created_at is None:
        return None
    material = "\x1f".join(
        (event.provider, event.event_name, event.entity_id, str(event.created_at))
    )
    return DERIVED_EVENT_ID_PREFIX + hashlib.sha256(material.encode()).hexdigest()


def resolve_event_id(*, header: str | None, event: WebhookEvent) -> str | None:
    """The id a webhook is deduplicated on, or None if it has none — in which case the service
    refuses it (400) rather than key it on ''.

    In order: the transport's delivery id (Razorpay: `X-Razorpay-Event-Id`), then an id the body
    itself carries, then `derive_event_id`. The header is **not** covered by the provider's HMAC —
    only the body is — so it is a delivery id and nothing more: reconciliation is idempotent on the
    charge's state regardless (a redelivery under a fresh id is a no-op), and the derived id is
    computed from signed bytes only.
    """
    return (
        usable_event_id(header)
        or usable_event_id(event.provider_event_id)
        or derive_event_id(event)
    )


# --- permanent webhook conditions --------------------------------------------------------

#: The stable reason codes an inbox row (and its audit row and log line) carries. Codes, not
#: messages: they are what an operator groups by, and they never hold a body or a person.
REASON_UNATTRIBUTABLE = "unattributable"  # no usable organization in `notes` — logged, never stored
REASON_UNKNOWN_CHARGE = "unknown_charge"
REASON_ILLEGAL_TRANSITION = "illegal_transition"
REASON_AMOUNT_MISMATCH = "amount_mismatch"
REASON_CURRENCY_MISMATCH = "currency_mismatch"
REASON_PROVIDER_ID_CONFLICT = "provider_id_conflict"


class WebhookNotApplied(Exception):
    """An authentic webhook that cannot be reconciled, and never will be. **Not** a `ProblemError`:
    it must never reach the HTTP layer as a 4xx, because the provider would redeliver it for days
    and nothing would be recorded. `ingest_webhook` catches it, rolls the attempt back to its
    SAVEPOINT and records the inbox row as `disposition` with `reason` instead.

    `facts` are small, PII-free values for the audit row (a state, a target, two amounts) — never a
    body, never free text from the provider.
    """

    def __init__(self, disposition: str, reason: str, facts: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.disposition = disposition
        self.reason = reason
        self.facts: dict[str, Any] = facts or {}


def unknown_charge() -> WebhookNotApplied:
    return WebhookNotApplied(INBOX_IGNORED, REASON_UNKNOWN_CHARGE)


def illegal_transition(from_state: str, to_state: str) -> WebhookNotApplied:
    return WebhookNotApplied(
        INBOX_CONFLICT,
        REASON_ILLEGAL_TRANSITION,
        {"charge_state": from_state, "asserted_state": to_state},
    )


def contradiction(
    *,
    charge_amount_minor: int,
    charge_currency: str,
    event_amount_minor: int | None,
    event_currency: str | None,
) -> WebhookNotApplied | None:
    """The verdict on a webhook whose payment is not the charge it claims to settle: another
    currency, or another amount. None when they agree, **or when the body does not say** — a field
    the provider left out is not a mismatch, and inventing one would refuse a genuine capture.

    Currency first: an amount in another currency is not comparable, so it is the more basic
    contradiction. Money is integer minor units on both sides, so equality is exact."""
    if event_currency is not None and event_currency.upper() != charge_currency.upper():
        return WebhookNotApplied(
            INBOX_CONFLICT,
            REASON_CURRENCY_MISMATCH,
            {
                "charge_currency": charge_currency.upper(),
                "asserted_currency": event_currency.upper(),
            },
        )
    if event_amount_minor is not None and event_amount_minor != charge_amount_minor:
        return WebhookNotApplied(
            INBOX_CONFLICT,
            REASON_AMOUNT_MISMATCH,
            {
                "charge_amount_minor": charge_amount_minor,
                "asserted_amount_minor": event_amount_minor,
            },
        )
    return None
