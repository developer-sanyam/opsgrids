"""Summary-metric computation for I5 (operations), I6 (projects) and I7 (storefront) —
`docs/insights/metrics.md` §4.3-4.5. Kept out of `app/selectors.py` so that module's own diff
stays the gate/scope chokepoint plus dispatch, not thirty per-metric queries.

**Scope of this slice.** Every value below is real, computed from the fact tables this slice
folds (never hardcoded — CLAUDE.md). Two things `metrics.md` also promises are **not** yet wired
here, and are called out rather than faked:

- **`other_currencies`** — every money metric below reports only the organization's own
  currency; a purchase order or enquiry in a different currency is not yet split out. Real
  fixtures in this dataset are single-currency, so this does not show up as a wrong number, only
  a narrower one than the eventual contract.
- **`metric_coverage`/`history_from`** — nothing yet writes a coverage row for any key here
  (that lands with the backfill's own "recompute coverage after a completed run" step), so
  `history_from` is always `None` and a state metric's comparison always reports
  `insufficient_history` rather than the anchor-day fallback `metrics.md` §2 describes. This is
  conservative — it never shows a fabricated delta — but it is not the full rule.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import false as sa_false
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, domain
from app.models import DailySnapshot, EnquiryFacts, PurchaseOrderFacts, TaskFacts
from app.schemas import ComparisonOut, MetricOut
from platform_core.auth import Scope


def _flow_comparison(current: float, previous: float | None) -> ComparisonOut:
    if previous is None:
        return ComparisonOut(state="insufficient_history")
    pct = domain.delta_pct(current, previous)
    return ComparisonOut(
        state="no_baseline" if pct is None else "ok",
        value=previous,
        delta_pct=pct,
    )


def _owned(stmt: Any, gate: access.Gate, owner_col: Any) -> Any:
    """The same OWN-narrowing `app/selectors.py:scoped` applies to a plain `select()`, spelled
    out here because these computations build more than one aggregate per gate (a window and a
    comparison window) and `scoped()` takes one statement at a time."""
    if gate.scope is Scope.OWN:
        if gate.subject_ref is None:
            # Nothing to own with — match nothing, never every row (the same safety
            # `ScopeContext.apply` and `scoped()` both give).
            return stmt.where(sa_false())
        return stmt.where(owner_col == gate.subject_ref)
    return stmt


def _scope_label(gate: access.Gate) -> str:
    return "OWN" if gate.scope is Scope.OWN else "ORG"


async def _purchase_orders_raised(
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
) -> MetricOut:
    async def count_in(w: domain.Window) -> int:
        stmt = (
            select(func.count())
            .select_from(PurchaseOrderFacts)
            .where(
                PurchaseOrderFacts.organization_id == organization_id,
                PurchaseOrderFacts.deleted_at.is_(None),
                PurchaseOrderFacts.status != "cancelled",
                PurchaseOrderFacts.created_date.between(w.from_, w.to),
            )
        )
        stmt = _owned(stmt, gate, PurchaseOrderFacts.owner_ref)
        return int((await session.execute(stmt)).scalar_one())

    value = await count_in(window)
    previous = await count_in(comparison_window) if comparison_window else None
    return MetricOut(
        state="ok",
        kind="flow",
        unit="count",
        scope=_scope_label(gate),
        value=value,
        comparison=_flow_comparison(value, previous),
    )


async def _purchase_orders_raised_value(
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
    currency: str,
) -> MetricOut:
    async def sum_in(w: domain.Window) -> int:
        stmt = select(func.coalesce(func.sum(PurchaseOrderFacts.total_minor), 0)).where(
            PurchaseOrderFacts.organization_id == organization_id,
            PurchaseOrderFacts.deleted_at.is_(None),
            PurchaseOrderFacts.status != "cancelled",
            PurchaseOrderFacts.created_date.between(w.from_, w.to),
            PurchaseOrderFacts.currency == currency,
        )
        stmt = _owned(stmt, gate, PurchaseOrderFacts.owner_ref)
        return int((await session.execute(stmt)).scalar_one())

    value = await sum_in(window)
    previous = await sum_in(comparison_window) if comparison_window else None
    return MetricOut(
        state="ok",
        kind="flow",
        unit="money",
        scope=_scope_label(gate),
        value=value,
        currency=currency,
        other_currencies=[],
        comparison=_flow_comparison(value, previous),
    )


async def _on_time_delivery(
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
) -> MetricOut:
    async def pct_in(w: domain.Window) -> float | None:
        stmt = select(
            func.count(),
            func.count().filter(
                PurchaseOrderFacts.received_date <= PurchaseOrderFacts.expected_date
            ),
        ).where(
            PurchaseOrderFacts.organization_id == organization_id,
            PurchaseOrderFacts.deleted_at.is_(None),
            PurchaseOrderFacts.received_date.between(w.from_, w.to),
        )
        stmt = _owned(stmt, gate, PurchaseOrderFacts.owner_ref)
        total, on_time = (await session.execute(stmt)).one()
        if total == 0:
            return None
        return round(float(on_time) / float(total) * 100, 1)

    value = await pct_in(window)
    previous = await pct_in(comparison_window) if comparison_window else None
    comparison = (
        ComparisonOut(state="insufficient_history")
        if comparison_window is None
        else ComparisonOut(
            state="ok" if previous is not None and value is not None else "no_baseline",
            value=previous,
            delta_pp=(
                domain.delta_pp(value, previous)
                if value is not None and previous is not None
                else None
            ),
        )
    )
    return MetricOut(
        state="ok",
        kind="percent",
        unit="percent",
        scope=_scope_label(gate),
        value=value,
        comparison=comparison,
    )


async def _latest_snapshot(
    session: AsyncSession, organization_id: uuid.UUID, metric: str
) -> list[DailySnapshot]:
    stmt = (
        select(DailySnapshot)
        .where(DailySnapshot.organization_id == organization_id, DailySnapshot.metric == metric)
        .order_by(DailySnapshot.day.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return []
    latest_day = rows[0].day
    return [r for r in rows if r.day == latest_day]


async def _stock_value(
    session: AsyncSession, organization_id: uuid.UUID, currency: str
) -> MetricOut:
    rows = await _latest_snapshot(session, organization_id, "stock_value")
    if not rows:
        return MetricOut(state="insufficient_history", kind="state", unit="money", scope="ORG")
    own = next((r for r in rows if r.dimension == currency), None)
    value = own.value if own is not None else 0
    as_of = max(r.captured_at for r in rows)
    return MetricOut(
        state="ok",
        kind="state",
        unit="money",
        scope="ORG",
        value=value,
        currency=currency,
        other_currencies=[],
        as_of=as_of,
        comparison=ComparisonOut(state="insufficient_history"),
    )


async def _snapshot_count(
    session: AsyncSession, organization_id: uuid.UUID, metric: str
) -> MetricOut:
    rows = await _latest_snapshot(session, organization_id, metric)
    if not rows:
        return MetricOut(state="insufficient_history", kind="state", unit="count", scope="ORG")
    row = rows[0]
    return MetricOut(
        state="ok",
        kind="state",
        unit="count",
        scope="ORG",
        value=row.value,
        as_of=row.captured_at,
        comparison=ComparisonOut(state="insufficient_history"),
    )


async def _tasks_flow(
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
    *,
    date_col: Any,
    extra_filter: Any = None,
    aggregate: Any = None,
) -> tuple[int, int | None]:
    async def value_in(w: domain.Window) -> int:
        agg = aggregate if aggregate is not None else func.count()
        stmt = (
            select(agg)
            .select_from(TaskFacts)
            .where(
                TaskFacts.organization_id == organization_id,
                TaskFacts.deleted_at.is_(None),
                date_col.between(w.from_, w.to),
            )
        )
        if extra_filter is not None:
            stmt = stmt.where(extra_filter)
        stmt = _owned(stmt, gate, TaskFacts.owner_ref)
        result = (await session.execute(stmt)).scalar_one()
        return int(result or 0)

    value = await value_in(window)
    previous = await value_in(comparison_window) if comparison_window else None
    return value, previous


async def _tasks_created(
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
) -> MetricOut:
    value, previous = await _tasks_flow(
        session,
        gate,
        organization_id,
        window,
        comparison_window,
        # `func.date(...)`, not the bare timestamp column: `.between(w.from_, w.to)` on a raw
        # timestamptz would silently exclude most of the `to` day (`metrics.md` §1's window is
        # `[from 00:00, (to + 1 day) 00:00)`, not `<= to`) — truncating first makes the same
        # `.between` correct at day granularity, matching `_tasks_completed`'s own `date_col`.
        date_col=func.date(TaskFacts.source_created_at),
    )
    return MetricOut(
        state="ok",
        kind="flow",
        unit="count",
        scope=_scope_label(gate),
        value=value,
        comparison=_flow_comparison(value, previous),
    )


async def _tasks_completed(
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
    *,
    points: bool,
) -> MetricOut:
    value, previous = await _tasks_flow(
        session,
        gate,
        organization_id,
        window,
        comparison_window,
        date_col=func.date(TaskFacts.completed_at),
        extra_filter=TaskFacts.stage == "done",
        aggregate=func.coalesce(func.sum(TaskFacts.points), 0) if points else None,
    )
    return MetricOut(
        state="ok",
        kind="flow",
        unit="count",
        scope=_scope_label(gate),
        value=value,
        comparison=_flow_comparison(value, previous),
    )


async def _overdue_tasks(
    session: AsyncSession, gate: access.Gate, organization_id: uuid.UUID, today: date
) -> MetricOut:
    stmt = (
        select(func.count())
        .select_from(TaskFacts)
        .where(
            TaskFacts.organization_id == organization_id,
            TaskFacts.deleted_at.is_(None),
            TaskFacts.stage != "done",
            TaskFacts.due_date.is_not(None),
            TaskFacts.due_date < today,
        )
    )
    stmt = _owned(stmt, gate, TaskFacts.owner_ref)
    value = (await session.execute(stmt)).scalar_one()
    return MetricOut(
        state="ok",
        kind="current",
        unit="count",
        scope=_scope_label(gate),
        value=value,
    )


async def _enquiries_received(
    session: AsyncSession,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
) -> MetricOut:
    async def count_in(w: domain.Window) -> int:
        # `source_created_at` is a timestamp; `metrics.md` §1 buckets one as
        # `[from 00:00 tz, (to + 1 day) 00:00 tz)` — the `+ 1 day` is load-bearing, or the whole
        # `to` day is silently excluded. Compared in UTC rather than the organization's own
        # timezone (a known simplification of this slice — see the module docstring).
        stmt = (
            select(func.count())
            .select_from(EnquiryFacts)
            .where(
                EnquiryFacts.organization_id == organization_id,
                EnquiryFacts.deleted_at.is_(None),
                EnquiryFacts.source_created_at >= w.from_,
                EnquiryFacts.source_created_at < w.to + timedelta(days=1),
            )
        )
        return (await session.execute(stmt)).scalar_one()

    value = await count_in(window)
    previous = await count_in(comparison_window) if comparison_window else None
    return MetricOut(
        state="ok",
        kind="flow",
        unit="count",
        scope="ORG",
        value=value,
        comparison=_flow_comparison(value, previous),
    )


async def compute(
    key: str,
    *,
    session: AsyncSession,
    gate: access.Gate,
    organization_id: uuid.UUID,
    window: domain.Window,
    comparison_window: domain.Window | None,
    currency: str,
    today: date,
) -> MetricOut | None:
    """`None` for a key this module does not (yet) compute — the caller falls back to
    `"unavailable"`, I1's own honest stub for the slices that have not landed."""
    if key == "purchase_orders_raised":
        return await _purchase_orders_raised(
            session, gate, organization_id, window, comparison_window
        )
    if key == "purchase_orders_raised_value":
        return await _purchase_orders_raised_value(
            session, gate, organization_id, window, comparison_window, currency
        )
    if key == "on_time_delivery":
        return await _on_time_delivery(session, gate, organization_id, window, comparison_window)
    if key == "stock_value":
        return await _stock_value(session, organization_id, currency)
    if key in ("low_stock_items", "out_of_stock_items"):
        return await _snapshot_count(session, organization_id, key)
    if key == "tasks_created":
        return await _tasks_created(session, gate, organization_id, window, comparison_window)
    if key == "tasks_completed":
        return await _tasks_completed(
            session, gate, organization_id, window, comparison_window, points=False
        )
    if key == "task_points_completed":
        return await _tasks_completed(
            session, gate, organization_id, window, comparison_window, points=True
        )
    if key == "open_tasks":
        return await _snapshot_count(session, organization_id, "open_tasks")
    if key == "overdue_tasks":
        return await _overdue_tasks(session, gate, organization_id, today)
    if key == "enquiries_received":
        return await _enquiries_received(session, organization_id, window, comparison_window)
    return None
