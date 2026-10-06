"""Resolving a subscription's pinned price from catalog — the number MRR multiplies.

A subscription transition event carries `plan_version_id` but **not** the price (the envelope is
thin on purpose, ADR-0004 §7). MRR is active subs times their pinned price (ADR-0004 §4), so the
fold has to resolve that price. It does so the way every cross-service read on this platform does:
an HTTP call to the owning service's internal API under a short-lived service token addressed to
that service by name — catalog's `GET /internal/v1/plan-versions/{id}` — never an import. That
keeps console coupled to catalog by the documented endpoint shape alone, so it builds and runs
before catalog's PR that adds the endpoint is merged.

**Producer-not-merged tolerance.** Until catalog exposes that internal read, or if catalog is
simply unreachable, this resolves to `(0, None)`: the subscription is still folded (its state,
entitlement and the transition row are all correct), it just contributes 0 to MRR until the price
can be resolved. This is logged, never raised — a projection consumer that dead-lettered on a
downstream being briefly unavailable would stall the whole stream. The price is re-resolved on the
next transition for that subscription, so MRR self-heals once the endpoint is live.

Documented response shape (from catalog's plan-version internal read):

    {"id": "...", "plan_id": "...", "billing_interval": "month", "currency": "INR",
     "prices": [{"kind": "flat", "amount_minor": 50000, "currency": "INR", ...}, ...]}

The resolved number is **monthly** recurring revenue: the sum of the **flat** prices'
`amount_minor` for one billing period, brought to one month by the version's `billing_interval`
(`_monthly_minor`). A yearly version's flat price buys twelve months, so it contributes a twelfth
of it — counting it whole would show a yearly subscriber's annual fee as a month's revenue, and
inflate every MRR figure (the org snapshot, the platform total, the series, the "Per month"
column) twelvefold for each one. A `one_time` version has no recurring period at all (the billing
run never bills it) and contributes nothing. Usage- and seat-priced components are not recurring
revenue and are excluded here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("console.price")

#: What the fold depends on: resolve a plan version id to `(amount_minor, currency)`. A callable so
#: the consumer injects the real HTTP resolver and tests inject a deterministic fake — the fold
#: itself does no I/O and needs no broker or catalog to unit-test.
PriceResolver = Callable[[str], Awaitable[tuple[int, str | None]]]

CATALOG_AUDIENCE = "catalog"


#: How many months one billing period of each cadence covers. Catalog's cadences are `month`,
#: `year` and `one_time` (`schemas.VersionCreateIn`); a document without one predates the field
#: and was monthly.
MONTHS_PER_PERIOD: dict[str, int] = {"month": 1, "year": 12}
ONE_TIME = "one_time"


def _monthly_minor(period_minor: int, billing_interval: str | None) -> int:
    """One period's recurring amount as a month's, in integer minor units (ADR-0000 §8).

    A yearly amount is divided by twelve, rounded half up to the nearest minor unit — integer
    arithmetic only, never a float. `one_time` is not recurring: 0.
    """
    if billing_interval == ONE_TIME:
        return 0
    months = MONTHS_PER_PERIOD.get(billing_interval or "month", 1)
    return (period_minor * 2 + months) // (months * 2)


def _flat_total(document: dict[str, Any]) -> tuple[int, str | None]:
    """The monthly recurring revenue one plan-version document is worth: its flat prices for one
    billing period, brought to a month by its cadence (`_monthly_minor`)."""
    amount = 0
    currency: str | None = document.get("currency")
    for price in document.get("prices", []):
        if price.get("kind") == "flat":
            amount += int(price.get("amount_minor") or 0)
            currency = price.get("currency") or currency
    return _monthly_minor(amount, document.get("billing_interval")), currency


async def resolve_plan_price(
    settings: Settings, plan_version_id: str, *, client: httpx.AsyncClient | None = None
) -> tuple[int, str | None]:
    """Resolve one plan version's pinned flat price, or `(0, None)` if it cannot be resolved."""
    url = f"{settings.catalog_internal_url}/internal/v1/plan-versions/{plan_version_id}"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=CATALOG_AUDIENCE, caller="console"
        )
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=5.0)
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return _flat_total(response.json())
    except Exception as exc:  # unreachable, 404 (endpoint not merged yet), malformed — tolerate
        log.warning(
            "price.unresolved",
            plan_version_id=plan_version_id,
            error=str(exc)[:200],
            hint="MRR omits this subscription until catalog's plan-version internal read resolves",
        )
        return 0, None
    finally:
        if owns_client:
            await client.aclose()


def make_resolver(settings: Settings) -> PriceResolver:
    """The real resolver the consumer folds with — each call opens and closes its own client."""

    async def _resolve(plan_version_id: str) -> tuple[int, str | None]:
        return await resolve_plan_price(settings, plan_version_id)

    return _resolve
