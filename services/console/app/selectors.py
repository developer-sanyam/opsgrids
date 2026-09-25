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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.models import AuditLog, OrgCommercialState, RevenueEvent, UsageSummary
from app.schemas import OrgStateOut

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


def lifecycle_of(state: OrgCommercialState) -> str:
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
        counts[lifecycle_of(state)] += 1
    return counts


def to_org_state_out(state: OrgCommercialState) -> OrgStateOut:
    """Shape one commercial snapshot the way the operator list and detail composite render it —
    `lifecycle` computed server-side (the same bucket `lifecycle_counts` uses, so the client never
    reimplements the rule), plus the mirrored identity fields. Not a plain
    `OrgStateOut.model_validate(state)`: the model's own `org_created_at` column is named to
    avoid colliding with `TenantBase`'s bookkeeping `created_at`, so it is remapped explicitly
    here rather than by an attribute-name coincidence.
    """
    return OrgStateOut(
        organization_id=state.organization_id,
        name=state.name,
        slug=state.slug,
        country=state.country,
        lifecycle=lifecycle_of(state),
        created_at=state.org_created_at,
        mrr_minor=state.mrr_minor,
        currency=state.currency,
        active_products=state.active_products,
        subscription_states=state.subscription_states,
        trial_ends_at=state.trial_ends_at,
        locked=state.locked,
        updated_at=state.updated_at,
    )


async def all_usage(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | None = None,
    limit: int | None = None,
    after: tuple[uuid.UUID, str, str] | None = None,
) -> Sequence[UsageSummary]:
    """Every organization's usage rollups, oldest-organization-first — optionally narrowed to
    one organization and paged with a keyset cursor on the same `(organization_id, meter_key,
    period)` ordering the unfiltered read already used, so an org-scoped page and the platform
    page agree on what "next" means. `limit=None` is the pre-pagination behaviour (every row,
    still used by the RLS-bypass companion test) — every real caller passes one.
    """
    stmt = select(UsageSummary).order_by(
        UsageSummary.organization_id, UsageSummary.meter_key, UsageSummary.period
    )
    if organization_id is not None:
        stmt = stmt.where(UsageSummary.organization_id == organization_id)
    if after is not None:
        after_org, after_meter, after_period = after
        stmt = stmt.where(
            tuple_(UsageSummary.organization_id, UsageSummary.meter_key, UsageSummary.period)
            > tuple_(literal(after_org), literal(after_meter), literal(after_period))
        )
    if limit is not None:
        stmt = stmt.limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def recent_audit(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | None = None,
    limit: int = 200,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> Sequence[AuditLog]:
    """The most recent operator-override audit rows, newest first — optionally narrowed to one
    organization and paged with a keyset cursor. Ordered `at DESC, id DESC`: several audit rows
    can share the same `at` (two overrides in the same request-second), so `id` is the
    tiebreaker a plain-`at` cursor would otherwise silently skip or repeat rows around.
    """
    stmt = select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
    if organization_id is not None:
        stmt = stmt.where(AuditLog.organization_id == organization_id)
    if after is not None:
        after_at, after_id = after
        stmt = stmt.where(
            tuple_(AuditLog.at, AuditLog.id) < tuple_(literal(after_at), literal(after_id))
        )
    return (await session.execute(stmt)).scalars().all()


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


async def metrics_series(session: AsyncSession, *, months: int) -> dict[str, Any]:
    """`GET /admin/metrics/series`'s whole payload: MRR, new organizations, trials started,
    conversions and churn, one point per month for the last `months` calendar months.

    Two independent sources, each shaped into a plain value before handing off to the pure
    replay in `domain.py` (this module does the I/O; `domain.commercial_series` decides the
    numbers): every subscription-lifecycle `revenue_events` row, oldest first, for the four
    commercial series, and every organization's own `org_created_at` for the new-organizations
    one — deliberately *not* from `revenue_events`, which never carries an organization's birth,
    only what it bought (see `domain.new_organizations_by_month`).
    """
    period_keys = domain.month_sequence(end=datetime.now(UTC), months=months)

    rows = (
        (
            await session.execute(
                select(RevenueEvent)
                .where(RevenueEvent.kind.in_(domain.SUBSCRIPTION_EVENTS))
                .order_by(RevenueEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    events = [
        domain.RevenueEventRecord(
            organization_id=str(row.organization_id),
            kind=row.kind,
            amount_minor=row.amount_minor,
            occurred_at=row.occurred_at.isoformat(),
            from_state=row.meta.get("from_state"),
            to_state=row.meta.get("to_state"),
            product_key=row.meta.get("product_key"),
            stale=bool(row.meta.get("stale", False)),
        )
        for row in rows
    ]
    series = domain.commercial_series(events, period_keys)

    created_ats = (
        (
            await session.execute(
                select(OrgCommercialState.org_created_at).where(
                    OrgCommercialState.org_created_at.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    new_organizations = domain.new_organizations_by_month(
        [c.isoformat() for c in created_ats if c is not None], period_keys
    )

    _, currency = (
        await session.execute(
            select(
                func.coalesce(func.sum(OrgCommercialState.mrr_minor), 0),
                func.min(OrgCommercialState.currency),
            )
        )
    ).one()

    return {
        "currency": currency,
        "periods": period_keys,
        "mrr_minor": series["mrr_minor"],
        "trials_started": series["trials_started"],
        "trial_conversions": series["trial_conversions"],
        "churned": series["churned"],
        "new_organizations": new_organizations,
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
