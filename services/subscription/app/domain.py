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

The **dunning ladder** (ADR-0005) lives here too, for the same reason: deciding *when* a
past-due subscription is retried and *when* the grace window closes is arithmetic over stored
timestamps, and arithmetic with no I/O in it is arithmetic a test can pin exactly. Nothing below
reads a clock — `now` is always passed in, and every due moment is anchored to the cycle's
**stored** `opened_at`, never to `now + interval`. That is what stops a sweep that ran three
hours late from pushing the whole remaining ladder three hours out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

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

#: The retry request the dunning sweep emits (ADR-0005). Not a lifecycle transition — the
#: subscription stays `past_due` while it is in flight — so it is deliberately *not* in
#: `_EVENT_BY_TARGET` and product-service does not bind it: nothing about entitlement changes
#: when a retry is asked for. payment-service consumes it and raises the charge.
EVT_PAYMENT_RETRY_DUE = EVT_PREFIX + "payment_retry_due"

#: Not a lifecycle transition either: the one-off backfill (`app/backfill.py`) emits it for a
#: bundle subscription that predates `entitled_products`, so the projection downstream learns the
#: members it has been entitled to all along. It carries the same payload as every lifecycle
#: event — state, pin, members — so product- and account-service fold it with the same handler.
EVT_ENTITLEMENTS_RESYNCED = EVT_PREFIX + "entitlements_resynced"

#: Reasons that override the target-state default event. An upgrade and a downgrade keep the
#: subscription `active`, so the target alone cannot tell them apart or from a renewal.
REASON_RENEW = "renew"
REASON_UPGRADE = "upgrade"
REASON_DOWNGRADE = "downgrade"

#: Dunning reasons, recorded on the transition row so the ladder is readable from the stream
#: alone: which failure opened it, which boundary closed it, and what ended it.
REASON_PAYMENT_FAILED = "payment_failed"
REASON_DUNNING_EXHAUSTED = "dunning_exhausted"
REASON_DUNNING_RECOVERED = "dunning_recovered"

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


# ------------------------------------------------------------------- bundles (commerce-packaging)

#: The feature key a bundle version declares its members in. Catalog's vocabulary, named here
#: rather than imported because one process may only ever hold one service's `app`.
FEATURE_BUNDLED_PRODUCTS = "bundled_products"

#: The shape of a product key — the gateway prefix, the permission prefix, the `ent` claim.
_PRODUCT_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


class MalformedBundle(BusinessRuleProblem):
    """A version whose `bundled_products` cannot be read as a list of product keys.

    Refused at subscribe rather than pinned: a bundle nobody can name the members of would be a
    subscription that bills for products it entitles nobody to — the exact failure this code
    exists to end. Catalog validates the declaration when it is authored; this is the net under
    that, for a version authored before the validation existed.
    """


def entitled_products(product_key: str, features: Iterable[tuple[str, Any]]) -> list[str]:
    """The product keys a subscription to this version entitles, in declaration order.

    A version that declares `bundled_products` entitles exactly its members — **not** the
    bundle's own key, which routes nowhere and would sit in the `ent` claim meaning nothing. Any
    other version entitles the product it was bought under, which is every subscription that
    existed before bundles fanned out.

    The list comes only from the pinned catalog version's feature sheet. The caller never gets a
    say: a member list read from a request body would let a customer buy the duo and name four
    products.
    """
    for key, value in features:
        if key != FEATURE_BUNDLED_PRODUCTS:
            continue
        if not isinstance(value, list) or not value:
            raise MalformedBundle(f"{FEATURE_BUNDLED_PRODUCTS} must be a non-empty list")
        members: list[str] = []
        for member in value:
            if not isinstance(member, str) or not _PRODUCT_KEY.match(member):
                raise MalformedBundle(
                    f"{FEATURE_BUNDLED_PRODUCTS} names {member!r}, which is not a product key"
                )
            if member not in members:
                members.append(member)
        return members
    return [product_key]


# --------------------------------------------------------------------------- dunning (ADR-0005)

#: What the sweep should do with one open cycle at one moment. Three outcomes and no fourth:
#: ask for the next debit, close the window, or come back later.
ACTION_RETRY = "retry"
ACTION_SUSPEND = "suspend"
ACTION_WAIT = "wait"


class InvalidPolicy(ValueError):
    """A ladder that cannot be executed — caught at import, not in production."""


@dataclass(frozen=True)
class DunningPolicy:
    """How many retries, when, and how long the grace window lasts (ADR-0005).

    `retry_offsets` are measured **from the first failure**, not from each other, so the whole
    ladder is a function of one stored instant. `grace` is measured from the same instant and is
    the single boundary at which entitlement stops.
    """

    retry_offsets: tuple[timedelta, ...]
    grace: timedelta

    def __post_init__(self) -> None:
        if not self.retry_offsets:
            raise InvalidPolicy("a dunning policy with no retries is an immediate suspension")
        if any(o <= timedelta(0) for o in self.retry_offsets):
            raise InvalidPolicy("a retry offset must be after the failure that opened the cycle")
        if list(self.retry_offsets) != sorted(self.retry_offsets):
            raise InvalidPolicy("retry offsets must be strictly increasing")
        if len(set(self.retry_offsets)) != len(self.retry_offsets):
            raise InvalidPolicy("two retries at the same offset is one retry and one wasted debit")
        if self.retry_offsets[-1] >= self.grace:
            # A last attempt outside the window would suspend before its own outcome is known,
            # which makes the final "access ends on <date>" notice a lie.
            raise InvalidPolicy("the last retry must fall inside the grace window")

    @property
    def max_attempts(self) -> int:
        return len(self.retry_offsets)


#: The platform default, fixed by ADR-0005: three retries at T+3d, T+7d and T+12d from the first
#: failure, and the window closes at T+14d.
#:
#: The spacing is set by the rails, not by taste. A UPI Autopay or eMandate debit needs a
#: pre-debit notice a clear day ahead, a fresh eMandate is not debitable for days after it is
#: created, and a debit over the UPI Autopay per-cycle ceiling needs the customer to
#: authenticate by hand — so an hourly ladder is not "aggressive", it is a ladder of debits the
#: rails will refuse and the sponsor bank will charge for. Days are the smallest honest unit.
#: 14 days is half a monthly period: the most unbilled service a customer can accumulate is
#: capped below one cycle, so two dunning cycles for the same subscription can never overlap.
POLICY = DunningPolicy(
    retry_offsets=(timedelta(days=3), timedelta(days=7), timedelta(days=12)),
    grace=timedelta(days=14),
)


def grace_ends_at(opened_at: datetime, policy: DunningPolicy = POLICY) -> datetime:
    """When entitlement stops if nothing is collected. Computed **once**, at open, and stored on
    the cycle — so re-reading it later cannot be affected by a policy edit or a late sweep."""
    return opened_at + policy.grace


def attempt_due_at(
    opened_at: datetime, attempt_no: int, policy: DunningPolicy = POLICY
) -> datetime | None:
    """When retry `attempt_no` (1-based) falls due, or None once the ladder is spent.

    Anchored to `opened_at`. A sweep that is late does not move the ladder: attempt 2 is still
    `opened_at + 7d` whether the sweep noticed at T+3d or T+3d+6h.
    """
    if attempt_no < 1 or attempt_no > policy.max_attempts:
        return None
    return opened_at + policy.retry_offsets[attempt_no - 1]


def next_action_at(
    opened_at: datetime,
    attempts_made: int,
    window_ends_at: datetime,
    policy: DunningPolicy = POLICY,
) -> datetime:
    """The next moment the sweep needs to look at this cycle.

    Either the next retry, or — once the ladder is spent — the close of the window, which is the
    suspension. Never past the window: a retry that would land after entitlement stops is not a
    retry anyone is waiting for.
    """
    due = attempt_due_at(opened_at, attempts_made + 1, policy)
    if due is None or due >= window_ends_at:
        return window_ends_at
    return due


def next_dunning_action(
    *,
    opened_at: datetime,
    window_ends_at: datetime,
    attempts_made: int,
    now: datetime,
    policy: DunningPolicy = POLICY,
) -> str:
    """What to do with one open cycle at `now`: retry, suspend, or wait.

    The window dominates: once `now` is past `window_ends_at` the answer is `suspend` whatever
    the ladder has left, because the window is the promise made to the customer and the ladder is
    only how the promise is spent. The reverse does not hold — a spent ladder inside an open
    window is `wait`, not an early suspension. That gap is deliberate (ADR-0005): the last
    attempt is scheduled two days before the window closes precisely so its outcome is known
    while there is still time to say "access ends on Thursday" and be telling the truth.
    """
    if now >= window_ends_at:
        return ACTION_SUSPEND
    due = attempt_due_at(opened_at, attempts_made + 1, policy)
    if due is None:
        return ACTION_WAIT
    return ACTION_RETRY if now >= due else ACTION_WAIT


def retry_idempotency_key(cycle_id: object, attempt_no: int) -> str:
    """The platform-supplied key each retry debit is raised under.

    Razorpay has no native idempotency key (ADR-0004 §6), so payment-service enforces one of
    ours with a unique constraint. It follows that a retry must **not** reuse the failed charge's
    key: re-sending it would collide with the charge that already failed and be deduplicated into
    a no-op — a dunning ladder that silently never debits. One key per attempt, derived from the
    cycle so it is stable under redelivery and reconstructable from the event stream.
    """
    return f"dunning:{cycle_id}:{attempt_no}"
