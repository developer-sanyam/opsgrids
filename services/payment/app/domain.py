"""The charge state machine and the reconciliation mapping. Pure — no I/O, no session, no clock.

Two things are decided here and nothing else:

1. **Is a charge move legal?** A charge is `created`, becomes `authorized`, and reaches a terminal
   `captured` or `failed`. `captured -> failed` is not a thing that can happen — a captured
   payment is money in the account — and pretending otherwise is how the subscription downstream
   comes to disagree with what was actually collected. The allowed edges are a small closed table.

2. **What does a reconciled outcome emit?** A webhook that captures a charge emits
   `commerce.payment.captured`; one that fails it emits `commerce.payment.failed`, and *that* is
   the event the subscription service turns into a suspension, a de-entitled activation, and a 402
   (ADR-0004 §5). An `authorized` intermediate emits nothing — it is not yet a fact anyone
   downstream acts on.

Kept pure so it is trivially testable and cannot grow a side effect: `services.py` is where the
row, the audit entry and the outbox event are staged, all in one transaction.
"""

from __future__ import annotations

from datetime import timedelta

from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_FAILED,
)
from platform_core.errors import BusinessRuleProblem

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


class IllegalChargeTransition(BusinessRuleProblem):
    """A charge move the state machine forbids — a 422, not a 500: the request was well-formed,
    the lifecycle simply does not allow it."""


def is_legal(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED.get(from_state, frozenset())


def assert_legal(from_state: str, to_state: str) -> None:
    if not is_legal(from_state, to_state):
        raise IllegalChargeTransition(f"cannot move a charge from {from_state!s} to {to_state!s}")


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
