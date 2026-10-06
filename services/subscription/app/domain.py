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
    PERIOD_KIND_CLOSE,
    PERIOD_KIND_OPENING,
    PERIOD_KIND_PRORATION,
    PERIOD_KIND_RENEWAL,
    PERIOD_KIND_TRIAL_CONVERSION,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
)
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem

# ------------------------------------------------------------------- stable refusal codes

#: A refusal an operator surface has to tell apart from another one: the body carries
#: `errors: [{"code": ..., "message": ...}]` (the shape every other service's coded refusal uses),
#: so a screen switches on the code and shows the message, instead of matching prose or folding
#: every 422 into one outcome. The set is a contract — add to it, never rename.
CODE_STATE = "state"  # the subscription is past_due / suspended / cancelled
CODE_CADENCE = "cadence"  # a different billing cadence, or one_time
CODE_CURRENCY = "currency"  # a different currency
CODE_ALREADY_PINNED = "already_pinned"
CODE_PRODUCT_MISMATCH = "product_mismatch"  # a version of another product
CODE_NOT_PUBLISHED = "not_published"
#: No such version — and also another organization's bespoke version, so that its existence is
#: never disclosed (a distinct `not_visible` code would disclose it).
CODE_NOT_FOUND = "not_found"
CODE_BILLING_CATCHING_UP = "billing_catching_up"  # 409: a period is due and not yet claimed
CODE_STATE_CHANGED = "state_changed"  # 409: the caller's `expected` no longer holds
CODE_RESERVED_REASON = "reserved_reason"
CODE_BUNDLE = "bundle"  # a bundle the product registry cannot vouch for
#: 409: the organization already has a row for that product — in any state, `cancelled` included
#: (one row per product per organization; reopening a cancelled one is a later decision).
CODE_ALREADY_SUBSCRIBED = "already_subscribed"
#: 422: a staff-opened subscription in a currency the organization's other live subscriptions are
#: not billed in (console's MRR sums one currency per organization).
CODE_ORG_CURRENCY_MISMATCH = "org_currency_mismatch"
#: 422: a `one_time` version has no period to bill and is never invoiced by the run.
CODE_ONE_TIME = "one_time"
#: 422: a trial was asked for on a version that sells none (`trial_days` 0).
CODE_TRIAL_NOT_OFFERED = "trial_not_offered"
#: 422: a trial extension on a subscription that is not trialling.
CODE_NOT_TRIAL = "not_trial"
#: 422: a trial extension while the run holds a claim on this subscription's conversion or
#: renewal — extending would void a conversion already being priced.
CODE_CLAIM_IN_FLIGHT = "claim_in_flight"
#: 422: a trial extension past `MAX_TOTAL_TRIAL_DAYS` from the trial's start.
CODE_TRIAL_CAP = "trial_cap"
#: 422: a trial "extension" that ends no later than the trial already does, or not in the future.
CODE_TRIAL_NOT_EXTENDED = "trial_not_extended"


class CodedRefusal(BusinessRuleProblem):
    """A 422 business-rule refusal with a stable machine code (see the codes above)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail, errors=[{"code": code, "message": detail}])
        self.code = code


class CodedConflict(ConflictProblem):
    """A 409 with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail, errors=[{"code": code, "message": detail}])
        self.code = code


class CodedNotFound(NotFoundProblem):
    """A 404 with a stable machine code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail, errors=[{"code": code, "message": detail}])
        self.code = code


EVT_PREFIX = "commerce.subscription."
EVT_TRIAL_STARTED = EVT_PREFIX + "trial_started"
EVT_ACTIVATED = EVT_PREFIX + "activated"
EVT_RENEWED = EVT_PREFIX + "renewed"
EVT_UPGRADED = EVT_PREFIX + "upgraded"
EVT_DOWNGRADED = EVT_PREFIX + "downgraded"
EVT_PAST_DUE = EVT_PREFIX + "past_due"
EVT_SUSPENDED = EVT_PREFIX + "suspended"
EVT_CANCELLED = EVT_PREFIX + "cancelled"
#: Staff moved a trialling subscription's end date later. A `trial -> trial` self-edge that is
#: deliberately **not** `trial_started`: console counts trial starts from that key and notification
#: rules fire on it, so reusing it would count one trial twice and mail a "welcome to your trial"
#: for an extension. Carries the full lifecycle payload plus `previous_trial_ends_at`.
EVT_TRIAL_EXTENDED = EVT_PREFIX + "trial_extended"

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

#: The billing run's reasons (ADR-0005 addendum, 2026-09-23). A trial that ends and is
#: collectable converts; one that ends with nothing to debit opens a grace window instead
#: (ADR-0013 §6) — a different reason on a different edge, so trial-conversion reporting
#: (`trial -> active` over trial starts, ADR-0004 §4) never counts an unpaid trial as converted.
REASON_TRIAL_CONVERT = "trial_convert"
REASON_TRIAL_UNPAID = "trial_ended_unpaid"
#: A staff trial extension (`trial -> trial`), typed as its own event (`EVT_TRIAL_EXTENDED`).
REASON_TRIAL_EXTENDED = "trial_extended"

#: Reasons only the machinery writes. An operator's free-text reason may not borrow one: the
#: transition stream would then say "dunning suspended this" of a person's hold (final money
#: review). Which suspension is in force is decided by the transition's **actor**, not its reason
#: — this keeps the stream honest for the humans who read it.
RESERVED_REASONS = frozenset(
    {
        REASON_RENEW,
        REASON_UPGRADE,
        REASON_DOWNGRADE,
        REASON_PAYMENT_FAILED,
        REASON_DUNNING_EXHAUSTED,
        REASON_DUNNING_RECOVERED,
        REASON_TRIAL_CONVERT,
        REASON_TRIAL_UNPAID,
        REASON_TRIAL_EXTENDED,
        "grace_expired",
        "payment_captured",
        "pre_cutover",
    }
)


def assert_free_text_allowed(text: str | None) -> None:
    """Refuse free text that borrows a reason only the machinery writes.

    Applied to **every** write that takes human words — change-plan, subscribe, suspend,
    reactivate, cancel — so the audit trail never says "dunning suspended this" of a person's act,
    and so a free-text word can never name a different event than the one the move is.
    """
    if text is not None and text.strip().lower() in RESERVED_REASONS:
        raise CodedRefusal(
            CODE_RESERVED_REASON,
            f"{text!r} is a reason only the platform's own machinery records; "
            "describe the operator's reason in words of your own",
        )


#: The legal edges. A key of `None` is birth — a subscription that does not exist yet. `cancelled`
#: is terminal: it maps to nothing, which is what makes `cancel -> active` refused. Self-edges on
#: `trial`/`active` are the renewals and plan changes that keep the state and move only the pin.
#:
#: Two edges arrived with the billing run. `trial -> past_due` is a trial that ended with no
#: usable mandate (ADR-0013 §6): the grace window opens exactly as for a failed renewal. And
#: `past_due -> past_due` is a renewal while a window is open — the period rolls and is invoiced,
#: the window keeps its own schedule, and nothing about entitlement changes.
ALLOWED: dict[str | None, frozenset[str]] = {
    None: frozenset({STATE_TRIAL, STATE_ACTIVE}),
    STATE_TRIAL: frozenset(
        {STATE_TRIAL, STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED}
    ),
    STATE_ACTIVE: frozenset({STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED}),
    STATE_PAST_DUE: frozenset({STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED}),
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


class IllegalTransition(CodedRefusal):
    """A move the state machine forbids. A 422 to the caller, not a 500 — the request was
    well-formed, the lifecycle simply does not allow it. Code `state`."""

    def __init__(self, detail: str) -> None:
        super().__init__(CODE_STATE, detail)


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
    if reason == REASON_TRIAL_EXTENDED:
        return EVT_TRIAL_EXTENDED
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
    if reason == REASON_TRIAL_EXTENDED and not (from_state == to_state == STATE_TRIAL):
        # The extension event means "still trialling, ending later" and nothing else; on any other
        # edge it would announce a trial that is not there.
        raise IllegalTransition(
            f"a trial extension keeps a trial a trial; not {from_state or 'nonexistent'!s} "
            f"to {to_state!s}"
        )
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


def declares_bundle(features: Iterable[tuple[str, Any]]) -> bool:
    """Whether a version is a bundle rung: it names members. Catalog only lets a bundle
    product's plans declare them, and requires every bundle version to."""
    return any(key == FEATURE_BUNDLED_PRODUCTS for key, _ in features)


# ------------------------------------------------------------------------------------- trials

#: The feature a plan version sets its trial length with, in days. `0` means the version sells
#: no trial. Absent means the platform default below.
FEATURE_TRIAL_DAYS = "trial_days"

#: The platform default: the 14-day trial the marketplace offers (ADR-0013). A product that
#: wants another length publishes a version with `trial_days`; the customer never chooses.
DEFAULT_TRIAL_DAYS = 14
MAX_TRIAL_DAYS = 365


def trial_allowance(features: Iterable[tuple[str, Any]]) -> int:
    """How many trial days the pinned version grants. Read from the version, never the caller:
    a trial length a customer can type is a free year of every product."""
    for key, value in features:
        if key != FEATURE_TRIAL_DAYS:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_TRIAL_DAYS
        ):
            raise BusinessRuleProblem(
                f"{FEATURE_TRIAL_DAYS} must be a whole number of days from 0 to {MAX_TRIAL_DAYS}"
            )
        return value
    return DEFAULT_TRIAL_DAYS


def trial_days_granted(requested: int | None, allowance: int) -> int | None:
    """The trial a subscribe actually opens: none if none was asked for, otherwise the request
    capped at the version's allowance. A version that sells no trial refuses the request rather
    than quietly opening a paid subscription the caller did not ask for."""
    if not requested:
        return None
    if allowance == 0:
        raise CodedRefusal(CODE_TRIAL_NOT_OFFERED, "this plan version does not offer a trial")
    return min(requested, allowance)


#: The longest a trial may run **in total** once staff extend it, in days, measured from the
#: trial's start (`current_period_start`, which nothing moves while a subscription trials — there
#: is no separate trial-start column). An owner-adjustable default (ADR-0031), not a law: a later
#: release may make it per-product or per-plan. Distinct from `MAX_TRIAL_DAYS`, which bounds what
#: one *version* may sell, not how far an operator may stretch one trial.
MAX_TOTAL_TRIAL_DAYS = 90


def trial_extension_refusal(
    *,
    trial_started_at: datetime | None,
    current_trial_ends_at: datetime | None,
    new_trial_ends_at: datetime,
    now: datetime,
    max_total_days: int = MAX_TOTAL_TRIAL_DAYS,
) -> tuple[str, str] | None:
    """Why moving a trial's end to `new_trial_ends_at` is refused, or None when it may be.

    Pure, so the cap arithmetic is pinned exactly by a test: the new end must be **later** than the
    trial already ends and later than `now` (an extension that leaves the trial ended is not one),
    and no later than `max_total_days` after the trial began. A trial with no recorded start is
    refused rather than guessed at — the cap cannot be measured.
    """
    if current_trial_ends_at is not None and new_trial_ends_at <= current_trial_ends_at:
        return (
            CODE_TRIAL_NOT_EXTENDED,
            "the new trial end must be later than the trial already ends "
            f"({current_trial_ends_at.isoformat()})",
        )
    if new_trial_ends_at <= now:
        return CODE_TRIAL_NOT_EXTENDED, "the new trial end must be in the future"
    if trial_started_at is None:
        return CODE_TRIAL_CAP, "this trial has no recorded start, so its total length is unknown"
    cap = trial_started_at + timedelta(days=max_total_days)
    if new_trial_ends_at > cap:
        return (
            CODE_TRIAL_CAP,
            f"a trial may run at most {max_total_days} days in total; this one started "
            f"{trial_started_at.isoformat()}, so it can end no later than {cap.isoformat()}",
        )
    return None


def trial_cap_at(
    state: str, trial_started_at: datetime | None, max_total_days: int = MAX_TOTAL_TRIAL_DAYS
) -> datetime | None:
    """The latest a trialling subscription's end may be moved to — None when it is not a trial
    or has no recorded start. What an operator surface shows next to the date it asks for."""
    if state != STATE_TRIAL or trial_started_at is None:
        return None
    return trial_started_at + timedelta(days=max_total_days)


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


# ------------------------------------------------------ the billing run (ADR-0005 addendum)

#: Catalog's recurring cadences. `one_time` has no period to roll and is not billed by the run.
INTERVAL_MONTH = "month"
INTERVAL_YEAR = "year"
INTERVAL_ONE_TIME = "one_time"

#: The period lengths this service has always used (`current_period_end` at subscribe): thirty
#: days for a month, 365 for a year. Kept exactly, so the first period the run rolls is the same
#: length as the one subscribe opened. Calendar-month anchoring is a separate decision.
_PERIOD_LENGTH: dict[str, timedelta] = {
    INTERVAL_MONTH: timedelta(days=30),
    INTERVAL_YEAR: timedelta(days=365),
}

#: How long a claimed period is held against other replicas while its billing call is out. Long
#: enough for a slow catalog, short enough that a crashed sweep's claim is retried the same hour.
BILLING_LEASE = timedelta(minutes=10)
#: Backoff after a failed attempt: 5 minutes, doubling, capped at six hours — a data defect (an
#: item off the pin, a missing tax rate) should not be retried every pass, and a sibling outage
#: should be retried soon after it ends.
_BACKOFF_BASE = timedelta(minutes=5)
_BACKOFF_CAP = timedelta(hours=6)


def period_end(start: datetime, billing_interval: str | None) -> datetime | None:
    """The end of the period that opens at `start`, or None for a cadence with no period."""
    length = _PERIOD_LENGTH.get(billing_interval or "")
    return start + length if length is not None else None


def infer_interval(period_start: datetime | None, period_end_at: datetime | None) -> str | None:
    """The cadence of a subscription written before it recorded one, from its period's length.

    Every such row was opened by `subscribe` with exactly 30 or 365 days, so the length names the
    interval; anything else is not a period this service opened and is not guessed at.
    """
    if period_start is None or period_end_at is None:
        return None
    length = period_end_at - period_start
    for interval, expected in _PERIOD_LENGTH.items():
        if length == expected:
            return interval
    return None


def retry_backoff(attempts: int) -> timedelta:
    """The wait before retrying a period that has failed `attempts` times (1-based)."""
    exponent: int = max(0, min(attempts - 1, 12))
    step: timedelta = _BACKOFF_BASE * (1 << exponent)
    return step if step < _BACKOFF_CAP else _BACKOFF_CAP


@dataclass(frozen=True)
class DuePeriod:
    """The period the billing run owes an invoice for, decided from stored timestamps alone.

    `start`/`end` bill recurring prices in advance; `usage_start`/`usage_end` is the period that
    has just closed, billed in arrears — None on a trial conversion, because trial usage is free.
    """

    kind: str
    start: datetime
    #: None when the cadence has no period (`one_time`): the period is due and cannot be priced —
    #: the run records a permanent failure for it rather than guessing an end.
    end: datetime | None
    usage_start: datetime | None
    usage_end: datetime | None
    error: str | None = None

    @property
    def billable(self) -> bool:
        return self.end is not None


KIND_TRIAL_CONVERSION = PERIOD_KIND_TRIAL_CONVERSION
KIND_RENEWAL = PERIOD_KIND_RENEWAL
KIND_PRORATION = PERIOD_KIND_PRORATION
KIND_CLOSE = PERIOD_KIND_CLOSE
KIND_OPENING = PERIOD_KIND_OPENING

#: A pending lifecycle claim that has failed this often is reported as permanently failing by the
#: health probe even if each failure looked transient.
PERMANENT_AFTER_ATTEMPTS = 5

#: States whose period rolls. `suspended` rolls too — but is not invoiced (see `bills_period`).
_ROLLING_STATES = frozenset({STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED})


class NotBillable(BusinessRuleProblem):
    """A due subscription the run cannot price a period for — a `one_time` cadence, or a row
    with no period at all. Recorded as a failure a person looks at, never invoiced by guess."""


def due_period(
    *,
    state: str,
    trial_ends_at: datetime | None,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    billing_interval: str | None,
    now: datetime,
    usage_billed_through: datetime | None = None,
) -> DuePeriod | None:
    """What, if anything, the billing run owes this subscription at `now`.

    * **A trial whose end has passed** converts: the first paid period opens *at the trial's
      end*, not at `now` — a sweep that runs six hours late must not shift every later period by
      six hours (the ADR-0005 rule: boundaries come from stored timestamps).
    * **An active, past-due or suspended subscription whose period has closed** rolls: the next
      period opens *at the old one's end*, and the closed period is the usage window — from
      `usage_billed_through` instead of the period start when a closing period (a suspension's)
      already billed the first part of it.
    * **Anything else** — cancelled, a trial still running, a period still open — owes nothing.

    A due period whose cadence has no period at all comes back with `end=None` and an `error`,
    for the run to record as a failure rather than invoice by guess. One period per call: a sweep
    that was down for three months catches up one period per pass, each with its own key.
    """
    interval = billing_interval or infer_interval(current_period_start, current_period_end)
    unbillable = f"a {interval or 'unknown'} cadence has no period to bill"
    if state == STATE_TRIAL:
        if trial_ends_at is None or trial_ends_at > now:
            return None
        end = period_end(trial_ends_at, interval)
        return DuePeriod(
            KIND_TRIAL_CONVERSION,
            trial_ends_at,
            end,
            None,
            None,
            None if end else unbillable,
        )
    if state in _ROLLING_STATES:
        if current_period_end is None or current_period_end > now:
            return None
        end = period_end(current_period_end, interval)
        usage_start = current_period_start
        if (
            usage_billed_through is not None
            and usage_start is not None
            and usage_billed_through > usage_start
        ):
            usage_start = usage_billed_through
        usage_end: datetime | None = current_period_end
        if usage_start is None or usage_start >= current_period_end:
            usage_start, usage_end = None, None
        return DuePeriod(
            KIND_RENEWAL,
            current_period_end,
            end,
            usage_start,
            usage_end,
            None if end else unbillable,
        )
    return None


#: What the cutover does to a row the run is about to bill (final review, fix 3).
CUTOVER_SKIP = "skip"
CUTOVER_CLAMP = "clamp"
OUTCOME_PRE_CUTOVER = "pre_cutover"


def cutover_action(
    *,
    kind: str,
    period_start: datetime,
    usage_start: datetime | None,
    usage_end: datetime | None,
    not_before: datetime | None,
) -> str | None:
    """`skip`, `clamp` or None: how the billing cutover treats one row before it is billed.

    * Recurring money — a renewal, a trial conversion, a proration, an opening — is **skipped**
      when its period began before the cutover: an existing subscription starts paying at its
      first period after it, and nothing billed in advance is back-charged.
    * Usage — a closing window — is skipped when it ended by the cutover, and **clamped** to
      start at the cutover when it straddles it: usage from before billing went live is never
      billed, usage after it is. (A renewal's usage window is clamped where it is computed.)
    """
    if not_before is None:
        return None
    if kind == KIND_CLOSE:
        if usage_end is not None and usage_end <= not_before:
            return CUTOVER_SKIP
        if usage_start is not None and usage_start < not_before:
            return CUTOVER_CLAMP
        return None
    return CUTOVER_SKIP if period_start < not_before else None


def usage_floor(
    usage_billed_through: datetime | None, not_before: datetime | None
) -> datetime | None:
    """The earliest instant a renewal's usage window may start: what is already billed, and never
    before the cutover."""
    if not_before is None:
        return usage_billed_through
    if usage_billed_through is None:
        return not_before
    return max(usage_billed_through, not_before)


def closing_usage_window(
    *,
    state: str,
    current_period_start: datetime | None,
    usage_billed_through: datetime | None,
    at: datetime,
    claimed_usage_end: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """The usage-only window a stop (a cancellation, a suspension) or a pin change at `at` leaves
    unbilled, if any.

    Usage bills in arrears at the next roll — which never comes for a cancelled subscription, and
    is priced at the *new* pin after a plan change. So the event claims its own window. It starts
    at the **latest** of what is already covered (review round 2, fix M4): the period start, where
    the last close stopped (`usage_billed_through`), and the end of the usage window of any
    lifecycle claim already made (`claimed_usage_end`) — a renewal claimed but not yet recorded
    covers the previous period's usage, and billing it again here would bill it twice. Only a
    served state has usage to bill: a trial's is free; a suspended one's was closed when it was
    suspended.
    """
    if state not in (STATE_ACTIVE, STATE_PAST_DUE) or current_period_start is None:
        return None
    start = current_period_start
    for floor in (usage_billed_through, claimed_usage_end):
        if floor is not None and floor > start:
            start = floor
    if at <= start:
        return None
    return start, at


#: A period's length in days, for comparing prices across cadences without a float.
_INTERVAL_DAYS: dict[str, int] = {INTERVAL_MONTH: 30, INTERVAL_YEAR: 365}


class CadenceChangeRefused(CodedRefusal):
    """A plan change to a different billing cadence, or to `one_time` (review round 2, B1).

    A monthly period and a yearly one are not two prices for one thing a proration can net: the
    periods do not line up, and pricing one against the other mis-bills one side. The path is
    cancel and subscribe again.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(CODE_CADENCE, detail)


def assert_same_cadence(current: str | None, target: str | None) -> None:
    if target == INTERVAL_ONE_TIME or target not in _INTERVAL_DAYS:
        raise CadenceChangeRefused(
            f"cannot change to a {target or 'unknown'} plan: cancel and subscribe to it instead"
        )
    if current != target:
        raise CadenceChangeRefused(
            f"cannot change from a {current or 'unknown'} plan to a {target} one mid-subscription:"
            " cancel and subscribe to the new plan instead"
        )


def plan_change_direction(
    new_minor: int, new_interval: str, old_minor: int, old_interval: str
) -> int:
    """+1 an upgrade, -1 a downgrade, 0 a change that moves no money (addendum §4, round 2 B1).

    Prices are compared **per day** by cross-multiplying with each cadence's length in days —
    `new * old_days` against `old * new_days`, integers only — so the comparison is right even
    where the cadences differ (which the change-plan path now refuses anyway). Equal is its own
    answer: an equal-price switch re-pins and closes usage at the old pin but raises no proration.
    """
    left = new_minor * _INTERVAL_DAYS[old_interval]
    right = old_minor * _INTERVAL_DAYS[new_interval]
    return (left > right) - (left < right)


def bills_period(state: str) -> bool:
    """Whether a rolling period is invoiced. A suspended subscription's period still rolls, so a
    customer who pays in week six resumes on a current period rather than being invoiced for every
    month they were switched off — but it is not billed for time it is not served."""
    return state != STATE_SUSPENDED
