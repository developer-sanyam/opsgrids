"""Reads for console — every one of them cross-tenant, on the directory session.

The operator sees *across* organizations by design (ADR-0003 §2), so these selectors are meant to
be run on `get_directory_session`: the SELECT-only BYPASSRLS reader that resolves every tenant's
projection rows at once. Run on the application role instead, with no tenant GUC bound, they would
return nothing — which is the companion this service tests, because a bypass that reads the same
empty set the app role would is a bypass that proves nothing.

The metrics are queries over the append-only `revenue_events`, not counters kept up to date
(ADR-0004 §4): trial-conversion is `trial → active` transitions over `trial` starts, churn is
`active → cancelled`. The `from_state` each classification needs was derived by the fold and stored
in `meta`, so the metric is a `WHERE kind = … AND meta->>'from_state' = …` rather than a second
source of truth to keep accurate.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, OrgCommercialState, RevenueEvent, UsageSummary

TRIAL_STARTED = "commerce.subscription.trial_started"
ACTIVATED = "commerce.subscription.activated"
CANCELLED = "commerce.subscription.cancelled"

#: The audit action a staff-set bespoke price is recorded under (`services.record_bespoke_price`)
#: — also the source of `GET .../pricing`'s per-product `history`.
BESPOKE_PRICE_CREATED = "catalog.bespoke_price.created"


async def all_org_states(session: AsyncSession) -> Sequence[OrgCommercialState]:
    """Every organization's commercial snapshot, richest first — the operator list."""
    return (
        (
            await session.execute(
                select(OrgCommercialState).order_by(OrgCommercialState.mrr_minor.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_org_state(session: AsyncSession, organization_id: Any) -> OrgCommercialState | None:
    """One organization's commercial snapshot — the console half of the org detail composite
    (`GET /admin/organizations/{id}`); the identity half comes from account-service's own
    staff-only route, never from this schema."""
    return (
        await session.execute(
            select(OrgCommercialState).where(OrgCommercialState.organization_id == organization_id)
        )
    ).scalar_one_or_none()


#: An organization's lifecycle bucket, for the platform-overview counts. Derived from the same
#: `subscription_states` the org list already renders rather than a stored column, so there is
#: nothing to keep in sync: `locked` wins (a suspension de-entitles regardless of what is
#: trialling underneath), then any `active` product, then any `trial`, then — no live product but
#: history exists — `churned`, and finally `none` for an organization the fold has never touched.
LIFECYCLE_BUCKETS = ("suspended", "active", "trial", "churned", "none")


def _lifecycle_of(state: OrgCommercialState) -> str:
    if state.locked:
        return "suspended"
    statuses = {entry.get("state") for entry in state.subscription_states.values()}
    if "active" in statuses:
        return "active"
    if "trial" in statuses:
        return "trial"
    if statuses:
        return "churned"
    return "none"


async def lifecycle_counts(session: AsyncSession) -> dict[str, int]:
    """Organization counts by lifecycle bucket, for the platform metrics overview."""
    counts = dict.fromkeys(LIFECYCLE_BUCKETS, 0)
    for state in await all_org_states(session):
        counts[_lifecycle_of(state)] += 1
    return counts


async def all_usage(session: AsyncSession) -> Sequence[UsageSummary]:
    """Every organization's usage rollups."""
    return (
        (
            await session.execute(
                select(UsageSummary).order_by(
                    UsageSummary.organization_id, UsageSummary.meter_key, UsageSummary.period
                )
            )
        )
        .scalars()
        .all()
    )


async def recent_audit(session: AsyncSession, *, limit: int = 200) -> Sequence[AuditLog]:
    """The most recent operator-override audit rows across every organization."""
    return (
        (await session.execute(select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)))
        .scalars()
        .all()
    )


async def platform_metrics(session: AsyncSession) -> dict[str, Any]:
    """MRR, trial-conversion and churn across the whole platform (ADR-0004 §4).

    MRR is the sum of every organization's running MRR; trial-conversion and churn are counts over
    the append-only transition log. Returned as plain numbers plus their derived rates, so the
    caller renders and the test asserts against unambiguous integers.
    """
    mrr_total, currency = (
        await session.execute(
            select(
                func.coalesce(func.sum(OrgCommercialState.mrr_minor), 0),
                func.min(OrgCommercialState.currency),
            )
        )
    ).one()

    trials_started = (
        await session.execute(
            select(func.count()).select_from(RevenueEvent).where(RevenueEvent.kind == TRIAL_STARTED)
        )
    ).scalar_one()
    trial_conversions = (
        await session.execute(
            select(func.count())
            .select_from(RevenueEvent)
            .where(
                RevenueEvent.kind == ACTIVATED,
                RevenueEvent.meta["from_state"].astext == "trial",
            )
        )
    ).scalar_one()
    churned = (
        await session.execute(
            select(func.count())
            .select_from(RevenueEvent)
            .where(
                RevenueEvent.kind == CANCELLED,
                RevenueEvent.meta["from_state"].astext == "active",
            )
        )
    ).scalar_one()

    # The active base is the count of currently-active subscriptions across the snapshots — read
    # from the same rows already fetched cross-tenant, so no second scan.
    states = await all_org_states(session)
    active_count = sum(
        1
        for row in states
        for entry in row.subscription_states.values()
        if entry.get("state") == "active"
    )

    return {
        "mrr_minor": int(mrr_total),
        "currency": currency,
        "trials_started": int(trials_started),
        "trial_conversions": int(trial_conversions),
        "trial_conversion_rate": (
            round(trial_conversions / trials_started, 4) if trials_started else 0.0
        ),
        "active_subscriptions": active_count,
        "churned": int(churned),
        "churn_rate": (
            round(churned / (churned + active_count), 4) if (churned + active_count) else 0.0
        ),
    }


# --------------------------------------------------------------------- client pricing


async def pricing_history(
    session: AsyncSession, organization_id: uuid.UUID, product_key: str, *, limit: int = 20
) -> Sequence[AuditLog]:
    """This organization's past bespoke prices for one product, newest first — console's own
    audit trail is the source (`services.record_bespoke_price`), because an immutable catalog
    plan version carries no reason and no actor. Run under the app role with the transaction
    bound to `organization_id` (RLS), like every tenant read on this session.
    """
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.action == BESPOKE_PRICE_CREATED,
            AuditLog.after["product_key"].astext == product_key,
        )
        .order_by(AuditLog.at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def find_pricing_change_by_idempotency_key(
    session: AsyncSession, organization_id: uuid.UUID, idempotency_key: str
) -> AuditLog | None:
    """The recorded result of an earlier `POST .../pricing` carrying this `Idempotency-Key`, if
    one already ran to completion for this organization. `None` means either this is the first
    attempt, or an earlier one crashed before recording — both are safe to redo: catalog reuses a
    matching bespoke version by content, and subscription's re-pin is refused as a no-op once the
    subscription already holds the target pin (`app/api/routes.py`'s pricing module docstring).
    """
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.action == BESPOKE_PRICE_CREATED,
            AuditLog.after["idempotency_key"].astext == idempotency_key,
        )
        .order_by(AuditLog.at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()
