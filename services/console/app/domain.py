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
    neither can this. `created_at` is set once and, being a creation timestamp, never arrives a
    second time with a different value in practice — it rides the same guard as the others only
    so a stale replay can never touch anything, including a field it does carry.

    Returns ``(applied, name, slug, country, created_at)`` — the fields to write, or the
    existing ones unchanged when not applied. `created_at` travels as the ISO string the caller
    gave it; parsing into a `datetime` for the column is the caller's job (`services.py`), same
    as every other timestamp this module treats as an opaque ordering key.
    """
    if _is_stale({"occurred_at": current_synced_at}, occurred_at):
        return False, current_name, current_slug, current_country, current_created_at
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
