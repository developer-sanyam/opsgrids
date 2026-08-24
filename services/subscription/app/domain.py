"""The subscription lifecycle state machine. Pure — no I/O, no session, no clock reads.

This is the authority ADR-0004 §4 describes: *what this customer has and where they are in its
life*. It decides two things and nothing else:

1. **Is a move legal?** `trial -> active` is a conversion; `cancelled -> active` is not a thing
   that can happen — a cancelled subscription is gone, and pretending otherwise is how the
   entitlement projection downstream comes to disagree with reality. The allowed edges are a
   small closed table, checked here so an illegal transition is refused before any row is
   written or any event is emitted.

2. **Which event does it emit?** Every accepted transition maps to exactly one
   `commerce.subscription.*` type (ADR-0004 §4), so the transition stream the operator console
   and the product-service projection consume is complete and unambiguous from the first
   subscription.

It is deliberately pure so it is trivially testable and cannot grow a side effect: `services.py`
is where the transition row, the audit row and the outbox event are staged, all in one
transaction. The reason this layer knows about `plan_version_id` at all is that an upgrade and a
downgrade are the same *state* (a subscription that stays active) distinguished only by whether
the new pin costs more — a distinction the caller supplies as the `reason`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import (
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
)
from platform_core.errors import BusinessRuleProblem

EVT_PREFIX = "commerce.subscription."
EVT_TRIAL_STARTED = EVT_PREFIX + "trial_started"
EVT_ACTIVATED = EVT_PREFIX + "activated"
EVT_RENEWED = EVT_PREFIX + "renewed"
EVT_UPGRADED = EVT_PREFIX + "upgraded"
EVT_DOWNGRADED = EVT_PREFIX + "downgraded"
EVT_PAST_DUE = EVT_PREFIX + "past_due"
EVT_SUSPENDED = EVT_PREFIX + "suspended"
EVT_CANCELLED = EVT_PREFIX + "cancelled"

#: Reasons that override the target-state default event. An upgrade and a downgrade keep the
#: subscription `active`, so the target alone cannot tell them apart or from a renewal.
REASON_RENEW = "renew"
REASON_UPGRADE = "upgrade"
REASON_DOWNGRADE = "downgrade"

#: The legal edges. A key of `None` is birth — a subscription that does not exist yet. `cancelled`
#: is terminal: it maps to nothing, which is what makes `cancel -> active` refused. Self-edges on
#: `trial`/`active` are the renewals and plan changes that keep the state and move only the pin.
ALLOWED: dict[str | None, frozenset[str]] = {
    None: frozenset({STATE_TRIAL, STATE_ACTIVE}),
    STATE_TRIAL: frozenset({STATE_TRIAL, STATE_ACTIVE, STATE_SUSPENDED, STATE_CANCELLED}),
    STATE_ACTIVE: frozenset({STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED}),
    STATE_PAST_DUE: frozenset({STATE_ACTIVE, STATE_SUSPENDED, STATE_CANCELLED}),
    STATE_SUSPENDED: frozenset({STATE_ACTIVE, STATE_CANCELLED}),
    STATE_CANCELLED: frozenset(),
}

#: The event a move to each state emits, unless a `reason` overrides it (renew/upgrade/downgrade).
_EVENT_BY_TARGET: dict[str, str] = {
    STATE_TRIAL: EVT_TRIAL_STARTED,
    STATE_ACTIVE: EVT_ACTIVATED,
    STATE_PAST_DUE: EVT_PAST_DUE,
    STATE_SUSPENDED: EVT_SUSPENDED,
    STATE_CANCELLED: EVT_CANCELLED,
}


class IllegalTransition(BusinessRuleProblem):
    """A move the state machine forbids. A 422 to the caller, not a 500 — the request was
    well-formed, the lifecycle simply does not allow it."""


def is_legal(from_state: str | None, to_state: str) -> bool:
    return to_state in ALLOWED.get(from_state, frozenset())


def assert_legal(from_state: str | None, to_state: str) -> None:
    if not is_legal(from_state, to_state):
        raise IllegalTransition(
            f"cannot move a subscription from {from_state or 'nonexistent'!s} to {to_state!s}"
        )


def event_for(to_state: str, *, reason: str | None) -> str:
    """The `commerce.subscription.*` type this move emits.

    `reason` wins where the target state cannot disambiguate: an upgrade, a downgrade and a
    renewal all keep the subscription `active`, and they are three different events.
    """
    if reason == REASON_UPGRADE:
        return EVT_UPGRADED
    if reason == REASON_DOWNGRADE:
        return EVT_DOWNGRADED
    if reason == REASON_RENEW:
        return EVT_RENEWED
    return _EVENT_BY_TARGET[to_state]


@dataclass(frozen=True)
class Decision:
    """The verdict on one proposed transition: legal, and the event it emits."""

    from_state: str | None
    to_state: str
    event_type: str
    reason: str | None


def decide(*, from_state: str | None, to_state: str, reason: str | None = None) -> Decision:
    """Validate a proposed transition and resolve the event it emits.

    Raises `IllegalTransition` on a forbidden edge, so `services.py` never writes a row or stages
    an event for a move the lifecycle does not permit.
    """
    assert_legal(from_state, to_state)
    return Decision(
        from_state=from_state,
        to_state=to_state,
        event_type=event_for(to_state, reason=reason),
        reason=reason,
    )
