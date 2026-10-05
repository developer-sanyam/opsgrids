"""Pure fold logic — no I/O, no database, no HTTP.

How a commercial transition becomes an operator snapshot, expressed as functions over plain
dicts so the arithmetic is unit-testable without a database or a broker. The writes in
`services.py` and the price resolution in `price.py` do the I/O; this module only decides what
the numbers should be.

The resolved model (ADR-0004 §4, commerce plan):

* **MRR** is the running sum of a customer's *active* subscriptions priced at their pinned plan
  version. Only `active` pays — a `trial`, `past_due`, `suspended` or `cancelled` subscription
  contributes zero.
* **entitlement** (`active_products`) is broader than MRR: an unlapsed `trial` entitles too.
* **locked** tracks suspension — true while any subscription is suspended.
* **churn** is an `active → cancelled` transition; **trial-conversion** is `trial → active` over
  `trial` starts. Both are read later from the appended `revenue_events`, using the `from_state`
  this module derives from the prior snapshot (the thin event payload carries only the new state).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Subscription routing keys this service folds. Bound by the consumer; listed here so the set is
# in one place and the tests iterate it.
SUBSCRIPTION_EVENTS: tuple[str, ...] = (
    "commerce.subscription.trial_started",
    "commerce.subscription.activated",
    "commerce.subscription.renewed",
    "commerce.subscription.upgraded",
    "commerce.subscription.downgraded",
    "commerce.subscription.past_due",
    "commerce.subscription.suspended",
    "commerce.subscription.cancelled",
)
INVOICE_EVENTS: tuple[str, ...] = (
    "commerce.invoice.finalized",
    "commerce.invoice.voided",
)
CREDIT_EVENTS: tuple[str, ...] = (
    "commerce.credit.granted",
    "commerce.credit.consumed",
    "commerce.credit.reversed",
)
USAGE_EVENT = "commerce.usage.recorded"

#: The three kinds `commercial_series` classifies into trials-started/conversions/churn — the
#: same three literals `selectors.py`'s own `platform_metrics` (a point-in-time read) already
#: names; kept here too, rather than imported across, because `selectors.py` predates this
#: module needing them and both are the routing key verbatim, not a value that can drift.
TRIAL_STARTED_KIND = "commerce.subscription.trial_started"
ACTIVATED_KIND = "commerce.subscription.activated"
CANCELLED_KIND = "commerce.subscription.cancelled"

# account-service's identity events (documented, from account-service's `app/services.py`; not
# imported — the coupling is the routing key and the payload, same as every commerce event
# above). `core.org.created` carries `organization_id`, `slug`, `name` (and other fields console
# does not project); `core.org.updated` carries `organization_id`, `changed`, and `name` when
# `"name" in changed` (`null` otherwise) — `slug` never appears there because account's own
# `OrganizationPatch` carries no slug field, so it can never be in `changed`.
ORG_EVENTS: tuple[str, ...] = (
    "core.org.created",
    "core.org.updated",
)

#: States that pay: what MRR sums over.
MRR_STATES: frozenset[str] = frozenset({"active"})
#: States that entitle a product: `active`, or a trial that has not lapsed.
ENTITLED_STATES: frozenset[str] = frozenset({"active", "trial"})
#: A cancelled subscription contributes nothing to the snapshot. The entry is *kept* as a
#: tombstone rather than popped: its `occurred_at` is what stops a stale pre-cancellation
#: event, replayed late, from resurrecting the product.
TERMINAL_STATE = "cancelled"


def period_bucket(occurred_at: datetime) -> str:
    """Coarse `YYYY-MM` bucket a usage event rolls into. A trend, not a bill."""
    return f"{occurred_at.year:04d}-{occurred_at.month:02d}"


def pricing_request_fingerprint(
    *,
    organization_id: str,
    product_key: str,
    amount_minor: int,
    currency: str,
    billing_interval: str,
    apply_to_subscription: bool,
) -> str:
    """A stable digest of a `SetPricingIn` request, for the `Idempotency-Key` guard
    (`services.set_organization_pricing`).

    Deliberately excludes `reason` — free text an operator might phrase slightly differently on
    a retry should not turn a legitimate replay into a "key reused for a different request"
    conflict. Every field that decides the *money* is in the digest; the one field that only
    explains it is not.
    """
    material = json.dumps(
        {
            "organization_id": organization_id,
            "product_key": product_key,
            "amount_minor": amount_minor,
            "currency": currency,
            "billing_interval": billing_interval,
            "apply_to_subscription": apply_to_subscription,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def plan_change_fingerprint(
    *,
    organization_id: str,
    product_key: str,
    subscription_id: str,
    plan_version_id: str,
    expected_state: str,
    expected_plan_version_id: str,
    expected_pending_plan_version_id: str | None,
) -> str:
    """A stable digest of what a plan-change request *decided*, for the `Idempotency-Key` claim.

    The same key with a different digest is a 409 — never a replay of someone else's answer.
    Every field that decides what happens is in it: where, to which version, and the state the
    operator confirmed against. Deliberately **not** in it: `reason` (free text an operator may
    phrase slightly differently on a retry, which should stay a replay — the same call
    `pricing_request_fingerprint` makes) and `timing` (`auto` and `next_period` are the same rule,
    ADR-0029, so they are the same request).
    """
    material = json.dumps(
        {
            "action": "plan_change",
            "organization_id": organization_id,
            "product_key": product_key,
            "subscription_id": subscription_id,
            "plan_version_id": plan_version_id,
            "expected": {
                "state": expected_state,
                "plan_version_id": expected_plan_version_id,
                "pending_plan_version_id": expected_pending_plan_version_id,
            },
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def recurring_list_price_minor(prices: Sequence[dict[str, Any]]) -> int:
    """One period's recurring list price for **display**: flat and per-seat components at quantity
    one, usage ignored — the same rule subscription uses to classify a change's direction
    (`services._list_price_minor`), restated here because console cannot import another service's
    code. It labels a plan in the picker; it never decides direction (subscription's preview does)
    and never prices anything."""
    return sum(
        int(p.get("amount_minor") or 0) for p in prices if p.get("kind") in ("flat", "per_seat")
    )


def _is_stale(entry: dict[str, Any], occurred_at: str | None) -> bool:
    """True when `occurred_at` predates (or ties) what the entry already reflects.

    Parsed, not string-compared: both values come from `build_envelope`'s UTC isoformat
    today, but a lexicographic comparison would silently misorder the moment any producer
    serialises a different offset.
    """
    applied = entry.get("occurred_at")
    if not applied or not occurred_at:
        return False
    try:
        return datetime.fromisoformat(occurred_at) <= datetime.fromisoformat(applied)
    except ValueError:
        return False


def apply_subscription_transition(
    states: dict[str, Any],
    *,
    product_key: str,
    to_state: str,
    plan_version_id: str | None,
    amount_minor: int,
    currency: str | None,
    trial_ends_at: str | None,
    occurred_at: str | None = None,
) -> tuple[bool, str | None]:
    """Fold one subscription transition into `states`.

    Returns ``(applied, from_state)``. The previous state is what classifies the transition
    — `None` for a brand-new subscription, `"active"` when this is a churn, `"trial"` when
    this is a conversion.

    Not applied when the transition is **stale**: delivery is unordered across relay and
    consumer replicas and DLQ replays, and this fold used to be last-write-wins — a
    suspension delivered after the reactivation that superseded it left the org's snapshot
    `locked` and its MRR wrong until the next transition. An event whose `occurred_at` does
    not advance the entry's is dropped, and `states` is untouched.

    Mutates `states` in place otherwise: sets the product's new entry, or tombstones it on
    cancellation (state kept, contributes nothing to the snapshot) so the ordering guard
    survives the subscription it guards.
    """
    entry: dict[str, Any] = states.get(product_key, {})
    if _is_stale(entry, occurred_at):
        return False, None
    previous: str | None = entry.get("state")
    if to_state == TERMINAL_STATE:
        states[product_key] = {"state": TERMINAL_STATE, "occurred_at": occurred_at}
    else:
        states[product_key] = {
            "state": to_state,
            "plan_version_id": plan_version_id,
            "amount_minor": int(amount_minor),
            "currency": currency,
            "trial_ends_at": trial_ends_at,
            "occurred_at": occurred_at,
        }
    return True, previous


def apply_org_identity(
    *,
    current_name: str | None,
    current_slug: str | None,
    current_synced_at: str | None,
    name: str | None,
    slug: str | None,
    occurred_at: str | None,
    current_country: str | None = None,
    current_created_at: str | None = None,
    country: str | None = None,
    created_at: str | None = None,
) -> tuple[bool, str | None, str | None, str | None, str | None]:
    """Fold one org-identity source (a `core.org.*` event, or a backfill snapshot stamped with
    account's own `updated_at`) into the projection's `name`/`slug`/`country`/`created_at`.

    Same ordering guard as `apply_subscription_transition`, over the same kind of thin,
    unordered-delivery-prone signal: a source whose `occurred_at` does not advance
    `current_synced_at` is dropped, so a `core.org.created` redelivered after a later rename (or
    a backfill run after a real rename event has already landed) cannot resurrect the old name.
    Any of `name`/`slug`/`country`/`created_at` being `None` (an update that did not touch that
    field, or a backfill row that genuinely lacks one) leaves the corresponding current value
    alone rather than blanking it — a PATCH cannot null a field account-service's own way, and
    neither can this.

    **A gap is not stale.** The ordering guard exists so an *old* value cannot overwrite a newer
    one, but `country` and `created_at` were added to the projection after rows were already
    synced: for those rows the current value is not old, it is absent, and there is nothing for a
    snapshot to resurrect. So a stale source still fills a `country`/`created_at` the projection
    has no value for, and never overwrites one it has — which is what makes re-running the
    backfill after this column landed actually fill it (a re-run's `updated_at` ties the one
    already stored, and a tie is stale). `applied` stays `False` in that case: `name`/`slug` and
    `identity_synced_at` are untouched, and the caller tells a gap fill from a no-op by comparing
    the returned values to what it holds.

    Returns ``(applied, name, slug, country, created_at)`` — the fields to write, or the
    existing ones unchanged when not applied (bar the gap fill above). `created_at` travels as
    the ISO string the caller gave it; parsing into a `datetime` for the column is the caller's
    job (`services.py`), same as every other timestamp this module treats as an opaque ordering
    key.
    """
    if _is_stale({"occurred_at": current_synced_at}, occurred_at):
        return (
            False,
            current_name,
            current_slug,
            current_country if current_country is not None else country,
            current_created_at if current_created_at is not None else created_at,
        )
    return (
        True,
        name if name is not None else current_name,
        slug if slug is not None else current_slug,
        country if country is not None else current_country,
        created_at if created_at is not None else current_created_at,
    )


def recompute_snapshot(states: dict[str, Any]) -> dict[str, Any]:
    """Derive the org-level snapshot fields from the per-product `states`.

    Returns the fields to write onto `OrgCommercialState`: `mrr_minor`, `currency`,
    `active_products`, `locked`, `trial_ends_at`. MRR sums only paying subscriptions; entitlement
    is broader; locked reflects any suspension; the trial end is the nearest one still trialling.
    """
    mrr_minor = sum(
        int(entry.get("amount_minor", 0))
        for entry in states.values()
        if entry.get("state") in MRR_STATES
    )
    currency = next(
        (entry.get("currency") for entry in states.values() if entry.get("currency")), None
    )
    active_products = sorted(
        key for key, entry in states.items() if entry.get("state") in ENTITLED_STATES
    )
    locked = any(entry.get("state") == "suspended" for entry in states.values())
    trial_ends = [
        entry.get("trial_ends_at")
        for entry in states.values()
        if entry.get("state") == "trial" and entry.get("trial_ends_at")
    ]
    nearest_trial_end = min(trial_ends) if trial_ends else None
    return {
        "mrr_minor": mrr_minor,
        "currency": currency,
        "active_products": active_products,
        "locked": locked,
        "trial_ends_at": nearest_trial_end,
    }


# --------------------------------------------------------------------- monthly series


def month_key(at: datetime) -> str:
    """The coarse `YYYY-MM` bucket a moment falls into — the series' period label."""
    return f"{at.year:04d}-{at.month:02d}"


def month_sequence(*, end: datetime, months: int) -> list[str]:
    """The last `months` calendar months (`YYYY-MM`), oldest first, ending with `end`'s own
    month — `GET /admin/metrics/series`'s x-axis."""
    year, month = end.year, end.month
    keys = []
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(keys))


@dataclass(frozen=True)
class RevenueEventRecord:
    """One `revenue_events` row, narrowed to what the monthly series replays — a plain value
    the selector shapes an ORM row into, so `commercial_series` stays pure and DB-free."""

    organization_id: str
    kind: str
    amount_minor: int
    occurred_at: str
    from_state: str | None
    to_state: str | None
    product_key: str | None
    stale: bool


def commercial_series(
    events: Sequence[RevenueEventRecord], months: Sequence[str]
) -> dict[str, dict[str, int | None]]:
    """MRR, trials started, trial-conversions and churn, by month — replayed from the
    subscription-lifecycle rows of `revenue_events` alone (ADR-0004 §4: "these metrics are
    answerable historically… from the append-only log").

    **MRR** is reconstructed by replaying every transition through the same
    `apply_subscription_transition`/`recompute_snapshot` arithmetic the live fold uses, in
    `occurred_at` order, across every organization at once — not a second implementation, the
    same one, given the whole history instead of one event. **Trials started**, **conversions**
    and **churn** are simple monthly counts of the already-classified `from_state`/`stale`
    console recorded at fold time (the same predicate `selectors.platform_metrics` uses) —
    replaying `from_state` from scratch here would lose the context a backfilled organization's
    *first* live transition already had (`app/backfill.py` seeds `subscription_states` directly,
    writing no `revenue_events` row for it, so the live fold's own recorded `from_state` is the
    only place that context survives).

    **Known limitation, by design, not a bug to fix here:** an organization whose subscription
    predates console's own event queue and has **never transitioned since** contributes nothing
    to this series, in any month, because the backfill that seeded its snapshot deliberately
    writes no `revenue_events` row (`app/backfill.py`'s own docstring: a synthetic row would
    corrupt the append-only history with a transition that never happened). Its current MRR is
    still correct on `/admin/organizations` and `/admin/metrics/overview`, which read the live
    snapshot, not this replay — only the *history* this series draws from is blind to it before
    its first real transition. This is exactly why the series is `null`, not `0`, before the
    earliest recorded transition: a month with no visibility must never be shown as a month with
    nothing happening.

    Returns one `{period: value}` mapping per series, `value` is `None` for every requested month
    before the earliest transition on record (no visibility, not a real zero) and a real number —
    zero included — for every month from there on.
    """
    empty = dict.fromkeys(months, None)
    if not events:
        return {
            "mrr_minor": dict(empty),
            "trials_started": dict(empty),
            "trial_conversions": dict(empty),
            "churned": dict(empty),
        }

    ordered = sorted(events, key=lambda e: e.occurred_at)
    first_month = month_key(datetime.fromisoformat(ordered[0].occurred_at))

    org_states: dict[str, dict[str, Any]] = {}
    org_totals: dict[str, int] = {}
    running_mrr = 0

    mrr_minor: dict[str, int | None] = {}
    trials_started: dict[str, int | None] = dict.fromkeys(months, 0)
    trial_conversions: dict[str, int | None] = dict.fromkeys(months, 0)
    churned: dict[str, int | None] = dict.fromkeys(months, 0)

    idx = 0
    for month in months:
        if month < first_month:
            mrr_minor[month] = None
            trials_started[month] = None
            trial_conversions[month] = None
            churned[month] = None
            continue
        while idx < len(ordered) and (
            month_key(datetime.fromisoformat(ordered[idx].occurred_at)) <= month
        ):
            event = ordered[idx]
            idx += 1
            event_month = month_key(datetime.fromisoformat(event.occurred_at))
            if not event.stale:
                if event.kind == TRIAL_STARTED_KIND:
                    trials_started[event_month] = (trials_started[event_month] or 0) + 1
                elif event.kind == ACTIVATED_KIND and event.from_state == "trial":
                    trial_conversions[event_month] = (trial_conversions[event_month] or 0) + 1
                elif event.kind == CANCELLED_KIND and event.from_state == "active":
                    churned[event_month] = (churned[event_month] or 0) + 1

            states = org_states.setdefault(event.organization_id, {})
            applied, _previous = apply_subscription_transition(
                states,
                product_key=event.product_key or "",
                to_state=event.to_state or "",
                plan_version_id=None,
                amount_minor=event.amount_minor,
                currency=None,
                trial_ends_at=None,
                occurred_at=event.occurred_at,
            )
            if applied:
                snapshot = recompute_snapshot(states)
                new_total = snapshot["mrr_minor"]
                running_mrr += new_total - org_totals.get(event.organization_id, 0)
                org_totals[event.organization_id] = new_total
        mrr_minor[month] = running_mrr

    return {
        "mrr_minor": mrr_minor,
        "trials_started": trials_started,
        "trial_conversions": trial_conversions,
        "churned": churned,
    }


def new_organizations_by_month(
    created_ats: Sequence[str], months: Sequence[str]
) -> dict[str, int | None]:
    """New organizations by month, from the identity fold's `org_created_at` — never
    `revenue_events`, which only ever sees a *commercial* transition, not an organization's own
    birth. `None` before the earliest organization this projection has a creation time for; a
    real count, zero included, for every month from there on."""
    if not created_ats:
        return dict.fromkeys(months, None)

    first_month = month_key(datetime.fromisoformat(min(created_ats)))
    counts: dict[str, int | None] = dict.fromkeys(months, 0)
    for month in months:
        if month < first_month:
            counts[month] = None
    for created_at in created_ats:
        bucket = month_key(datetime.fromisoformat(created_at))
        if bucket in counts and counts[bucket] is not None:
            counts[bucket] = counts[bucket] + 1  # type: ignore[operator]
    return counts
