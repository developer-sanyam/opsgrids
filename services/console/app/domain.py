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
