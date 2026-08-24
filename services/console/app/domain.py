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
#: A cancelled subscription is removed from the snapshot — the customer no longer has it.
TERMINAL_STATE = "cancelled"


def period_bucket(occurred_at: datetime) -> str:
    """Coarse `YYYY-MM` bucket a usage event rolls into. A trend, not a bill."""
    return f"{occurred_at.year:04d}-{occurred_at.month:02d}"


def apply_subscription_transition(
    states: dict[str, Any],
    *,
    product_key: str,
    to_state: str,
    plan_version_id: str | None,
    amount_minor: int,
    currency: str | None,
    trial_ends_at: str | None,
) -> str | None:
    """Fold one subscription transition into `states`, returning the *from* state.

    Mutates `states` in place: sets the product's new entry, or removes it on cancellation. The
    returned previous state is what classifies the transition — `None` for a brand-new
    subscription, `"active"` when this is a churn, `"trial"` when this is a conversion.
    """
    previous: str | None = states.get(product_key, {}).get("state")
    if to_state == TERMINAL_STATE:
        states.pop(product_key, None)
    else:
        states[product_key] = {
            "state": to_state,
            "plan_version_id": plan_version_id,
            "amount_minor": int(amount_minor),
            "currency": currency,
            "trial_ends_at": trial_ends_at,
        }
    return previous


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
