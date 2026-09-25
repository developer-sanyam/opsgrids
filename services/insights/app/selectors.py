"""Reads for insights. Visibility rules live here, in one place (ADR-0020 §4, §7).

Every widget goes through `app/access.py:gate_*` before this module ever builds a query — a
`not_active`/`no_access` gate short-circuits before touching the database, matching
"authorization is server-side" (CLAUDE.md). `scoped()` is the chokepoint every I2+ fact query
starts from: it filters `organization_id` explicitly (RLS is the net, not the filter) and then,
for an OWN-scoped gate, narrows to the caller's own rows — the same rule
`platform_core.auth.ScopeContext.apply` enforces for every other service's list endpoint.

I1 has no entity fact table, so every widget whose gate resolves to `"ok"` reads as
`"unavailable"` here — there is nothing yet to compute (ADR-0020 build plan, I1: "every widget
renders its state. No figures yet."). I3 (deal) and I4 (lead, customer) are the first to
replace that with a real read, for `deals_created`, `deals_won`, `deals_won_value`,
`deals_lost`, `win_rate`, `pipeline_value`, `open_deals`, `weighted_pipeline`, `leads_created`,
`leads_converted` and `customers` — every other metric still answers `"unavailable"` until its
own slice lands; the gate, the scoping and the response shape do not change.

**Coverage, simplified for this slice.** `deals_won`/`deals_won_value`/`deals_lost`/`win_rate`
read `metric_coverage` if a row exists, and treat a missing row as complete (`history_from =
NULL`) rather than unknown — the full "recompute after every completed backfill run" step
(ADR-0020 §3) is not wired from the backfill runner in this slice (see the module's own
`crm_export.py`), so a fresh organization's coverage is optimistic until that lands. Snapshot
metrics (`pipeline_value`, `open_deals`, `weighted_pipeline`, `customers`) get theirs written by
`app/snapshot.py`'s own capture the moment its first row lands, which *is* wired.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, domain
from app.models import CompanyFact, DailySnapshot, DealFact, LeadFact, MetricCoverage, OrgProfile
from app.schemas import (
    ComparisonOut,
    MetricOut,
    OtherCurrencyValueOut,
    PeriodOut,
    RankingsOut,
    SeriesOut,
    SummaryOut,
)
from platform_core.auth import Principal, Scope

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_CURRENCY = "INR"
DEFAULT_FISCAL_YEAR_START_MONTH = 4


async def get_org_profile(session: AsyncSession, organization_id: uuid.UUID) -> OrgProfile | None:
    """`None` for an organization insights has not folded a profile for yet — every caller below
    falls back to the class defaults rather than treating that as an error (data-model.md §2)."""
    return await session.get(OrgProfile, organization_id)


def org_timezone(profile: OrgProfile | None) -> str:
    return profile.timezone if profile is not None else DEFAULT_TIMEZONE


def org_currency(profile: OrgProfile | None) -> str:
    return profile.currency if profile is not None else DEFAULT_CURRENCY


def org_fiscal_year_start_month(profile: OrgProfile | None) -> int:
    return (
        profile.fiscal_year_start_month if profile is not None else DEFAULT_FISCAL_YEAR_START_MONTH
    )


def local_today(timezone: str) -> date:
    """`(now() AT TIME ZONE tz)::date` (`docs/insights/metrics.md` §1) — computed fresh on every
    call, never cached and never derived from a stored value."""
    return datetime.now(ZoneInfo(timezone)).date()


def scoped[S: Select[Any]](
    stmt: S,
    gate: access.Gate,
    *,
    organization_id: uuid.UUID,
    org_col: Any,
    owner_col: Any | None = None,
) -> S:
    """The chokepoint every fact query (I2+) starts from. `gate.state` must already be `"ok"` —
    a `not_active`/`no_access` gate never reaches a query at all."""
    if gate.state != "ok":
        raise ValueError("scoped() takes only an 'ok' gate — check the gate before querying")
    stmt = stmt.where(org_col == organization_id)
    if gate.scope is Scope.OWN:
        if owner_col is None:
            raise ValueError("an OWN-scoped gate needs an owner_col to filter on")
        if gate.subject_ref is None:
            # Nothing to own with — match nothing, the same safety `ScopeContext.apply` uses:
            # `owner_col == NULL` would be SQL NULL and quietly return every row instead.
            return stmt.where(sa_false())
        return stmt.where(owner_col == gate.subject_ref)
    return stmt


def _scope_label(gate: access.Gate) -> str:
    return "OWN" if gate.scope is Scope.OWN else "ORG"


def _period_out(key: str, window: domain.Window) -> PeriodOut:
    return PeriodOut(
        key=key, from_date=window.from_, to=window.to, label=domain.window_label(window)
    )


def _utc_window(window: domain.Window, timezone: str) -> tuple[datetime, datetime]:
    """A local-date window as a half-open UTC instant range (`docs/insights/metrics.md` §1):
    `[from 00:00 tz, (to + 1 day) 00:00 tz)`, so a timestamp column's own org-leading index is
    used, not a per-row cast to the caller's timezone."""
    tz = ZoneInfo(timezone)
    start_local = datetime.combine(window.from_, time.min, tzinfo=tz)
    end_local = datetime.combine(window.to + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def _count_in_window(
    session: AsyncSession,
    model: Any,
    ts_col: Any,
    *,
    gate: access.Gate,
    organization_id: uuid.UUID,
    owner_col: Any | None,
    start: datetime,
    end: datetime,
    extra: tuple[Any, ...] = (),
) -> int:
    stmt = select(func.count()).select_from(model).where(model.deleted_at.is_(None))
    stmt = scoped(
        stmt,
        gate,
        organization_id=organization_id,
        org_col=model.organization_id,
        owner_col=owner_col,
    )
    stmt = stmt.where(ts_col >= start, ts_col < end, *extra)
    return (await session.execute(stmt)).scalar_one()


async def _money_by_currency_in_window(
    session: AsyncSession,
    model: Any,
    ts_col: Any,
    amount_col: Any,
    currency_col: Any,
    *,
    gate: access.Gate,
    organization_id: uuid.UUID,
    owner_col: Any | None,
    start: datetime,
    end: datetime,
    extra: tuple[Any, ...] = (),
) -> dict[str, int]:
    stmt = (
        select(currency_col, func.coalesce(func.sum(amount_col), 0))
        .where(model.deleted_at.is_(None))
        .group_by(currency_col)
    )
    stmt = scoped(
        stmt,
        gate,
        organization_id=organization_id,
        org_col=model.organization_id,
        owner_col=owner_col,
    )
    stmt = stmt.where(ts_col >= start, ts_col < end, *extra)
    rows = (await session.execute(stmt)).all()
    # A currency's group is omitted when its sum is 0 (`docs/insights/metrics.md` §1).
    return {str(currency): int(total) for currency, total in rows if int(total) != 0}


def _money_out(
    by_currency: dict[str, int], org_currency: str
) -> tuple[int, list[OtherCurrencyValueOut]]:
    value = by_currency.get(org_currency, 0)
    others = [
        OtherCurrencyValueOut(currency=currency, value=amount)
        for currency, amount in sorted(by_currency.items())
        if currency != org_currency
    ]
    return value, others


async def _coverage_history_from(
    session: AsyncSession, organization_id: uuid.UUID, metric: str
) -> date | None:
    """A missing row is treated as complete (`history_from = None`) — see this module's own
    docstring for why the "recompute after every completed run" step is not wired yet for the
    `source_column_from_go_live` metrics this slice adds."""
    row = await session.get(MetricCoverage, {"organization_id": organization_id, "metric": metric})
    return row.history_from if row is not None else None


async def _snapshot_comparison(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    metric: str,
    dimension: str,
    anchor_day: date,
) -> ComparisonOut:
    """The comparison value for a state metric (`docs/insights/metrics.md` §2): the capture of
    `anchor_day`, or the nearest earlier one within 3 days, else `insufficient_history`."""
    stmt = (
        select(DailySnapshot)
        .where(
            DailySnapshot.organization_id == organization_id,
            DailySnapshot.metric == metric,
            DailySnapshot.dimension == dimension,
            DailySnapshot.day <= anchor_day,
            DailySnapshot.day >= anchor_day - timedelta(days=3),
        )
        .order_by(DailySnapshot.day.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return ComparisonOut(state="insufficient_history")
    return ComparisonOut(
        state="ok", value=row.value, anchor_day=row.day if row.day != anchor_day else None
    )


#: Metric keys this slice (I3, I4) replaces `"unavailable"` with a real read for.
_DEAL_FLOW_METRICS = frozenset(
    {"deals_created", "deals_won", "deals_won_value", "deals_lost", "win_rate"}
)
_DEAL_STATE_METRICS = frozenset({"pipeline_value", "open_deals", "weighted_pipeline"})
_LEAD_FLOW_METRICS = frozenset({"leads_created", "leads_converted"})


async def _compute_deal_flow(
    session: AsyncSession,
    gate: access.Gate,
    key: str,
    *,
    organization_id: uuid.UUID,
    org_currency: str,
    start: datetime,
    end: datetime,
) -> tuple[int | float | None, str | None, list[OtherCurrencyValueOut] | None]:
    owner_col = DealFact.owner_ref
    if key == "deals_created":
        value = await _count_in_window(
            session,
            DealFact,
            DealFact.source_created_at,
            gate=gate,
            organization_id=organization_id,
            owner_col=owner_col,
            start=start,
            end=end,
        )
        return value, None, None
    if key == "deals_won":
        value = await _count_in_window(
            session,
            DealFact,
            DealFact.closed_at,
            gate=gate,
            organization_id=organization_id,
            owner_col=owner_col,
            start=start,
            end=end,
            extra=(DealFact.stage == "closed_won",),
        )
        return value, None, None
    if key == "deals_won_value":
        by_currency = await _money_by_currency_in_window(
            session,
            DealFact,
            DealFact.closed_at,
            DealFact.value_minor,
            DealFact.currency,
            gate=gate,
            organization_id=organization_id,
            owner_col=owner_col,
            start=start,
            end=end,
            extra=(DealFact.stage == "closed_won",),
        )
        value, others = _money_out(by_currency, org_currency)
        return value, org_currency, others
    if key == "deals_lost":
        value = await _count_in_window(
            session,
            DealFact,
            DealFact.closed_at,
            gate=gate,
            organization_id=organization_id,
            owner_col=owner_col,
            start=start,
            end=end,
            extra=(DealFact.stage == "closed_lost",),
        )
        return value, None, None
    # win_rate
    won = await _count_in_window(
        session,
        DealFact,
        DealFact.closed_at,
        gate=gate,
        organization_id=organization_id,
        owner_col=owner_col,
        start=start,
        end=end,
        extra=(DealFact.stage == "closed_won",),
    )
    lost = await _count_in_window(
        session,
        DealFact,
        DealFact.closed_at,
        gate=gate,
        organization_id=organization_id,
        owner_col=owner_col,
        start=start,
        end=end,
        extra=(DealFact.stage == "closed_lost",),
    )
    decided = won + lost
    # `null` when none were decided — no percentage a full open pipeline should imply
    # (`docs/insights/metrics.md` §4.2), never a fabricated 0%.
    rate = round(won / decided * 100, 1) if decided else None
    return rate, None, None


async def _compute_lead_flow(
    session: AsyncSession,
    gate: access.Gate,
    key: str,
    *,
    organization_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> int:
    owner_col = LeadFact.owner_ref
    ts_col = LeadFact.source_created_at if key == "leads_created" else LeadFact.converted_at
    return await _count_in_window(
        session,
        LeadFact,
        ts_col,
        gate=gate,
        organization_id=organization_id,
        owner_col=owner_col,
        start=start,
        end=end,
    )


async def _compute_deal_state(
    session: AsyncSession, key: str, *, organization_id: uuid.UUID, org_currency: str
) -> tuple[int, str | None, list[OtherCurrencyValueOut] | None]:
    """Live now (`docs/insights/metrics.md` §4.2) — `Σ value_minor`/count/weighted sum of
    non-deleted, non-closed deals. ORG-only, so no owner filter ever narrows this."""
    open_stmt = select(DealFact).where(
        DealFact.organization_id == organization_id,
        DealFact.deleted_at.is_(None),
        DealFact.stage.notin_(("closed_won", "closed_lost")),
    )
    open_deals = list((await session.execute(open_stmt)).scalars().all())
    if key == "open_deals":
        return len(open_deals), None, None
    by_currency: dict[str, int] = {}
    for deal in open_deals:
        if key == "pipeline_value":
            amount = deal.value_minor
        else:  # weighted_pipeline
            amount = domain.weighted_pipeline_minor(deal.value_minor, deal.probability)
        by_currency[deal.currency] = by_currency.get(deal.currency, 0) + amount
    value, others = _money_out(by_currency, org_currency)
    return value, org_currency, others


async def _summary_metric(
    session: AsyncSession,
    principal: Principal,
    key: str,
    *,
    timezone: str,
    org_currency: str,
    window: domain.Window,
    comparison_window: domain.Window | None,
) -> MetricOut:
    """One entry of `GET /summary`'s `metrics` map. `key` is assumed already validated against
    `app.access.METRICS` — an unknown key is a 422 at the route, never reaches here."""
    spec = access.METRICS[key]
    gate = access.gate_metric(principal, key)
    if gate.state == "not_active":
        return MetricOut(state="not_active", product_key=gate.product_key)
    if gate.state == "no_access":
        return MetricOut(state="no_access")

    organization_id = principal.organization_id
    scope_label = _scope_label(gate)

    if key not in _DEAL_FLOW_METRICS | _DEAL_STATE_METRICS | _LEAD_FLOW_METRICS | {"customers"}:
        # No fact table for this key yet (I2, I5-I9). An entitled, permitted caller sees an
        # honest "not computed yet", never a fabricated zero.
        return MetricOut(state="unavailable", kind=spec.kind, unit=spec.unit, scope=scope_label)

    value: int | float | None
    if key in _DEAL_STATE_METRICS or key == "customers":
        if key == "customers":
            stmt = (
                select(func.count())
                .select_from(CompanyFact)
                .where(
                    CompanyFact.organization_id == organization_id,
                    CompanyFact.deleted_at.is_(None),
                    CompanyFact.status == "customer",
                )
            )
            value = (await session.execute(stmt)).scalar_one()
            currency_out, others = None, None
        else:
            value, currency_out, others = await _compute_deal_state(
                session, key, organization_id=organization_id, org_currency=org_currency
            )
        comparison_out = None
        if comparison_window is not None:
            dimension = currency_out or ""
            comparison_out = await _snapshot_comparison(
                session,
                organization_id=organization_id,
                metric=key,
                dimension=dimension,
                anchor_day=comparison_window.to,
            )
            if comparison_out.state == "ok" and comparison_out.value is not None:
                comparison_out.delta_pct = domain.delta_pct(value, comparison_out.value)
        history_from = await _coverage_history_from(session, organization_id, key)
        return MetricOut(
            state="ok",
            kind=spec.kind,
            unit=spec.unit,
            scope=scope_label,
            value=value,
            currency=currency_out,
            other_currencies=others,
            comparison=comparison_out,
            history_from=history_from,
        )

    # Flow / percent metrics (deal or lead), gated by go-live coverage where it applies.
    history_from = await _coverage_history_from(session, organization_id, key)
    if history_from is not None and window.from_ < history_from:
        return MetricOut(
            state="insufficient_history", kind=spec.kind, unit=spec.unit, scope=scope_label
        )

    start, end = _utc_window(window, timezone)
    if key in _DEAL_FLOW_METRICS:
        value, currency_out, others = await _compute_deal_flow(
            session,
            gate,
            key,
            organization_id=organization_id,
            org_currency=org_currency,
            start=start,
            end=end,
        )
    else:
        value = await _compute_lead_flow(
            session, gate, key, organization_id=organization_id, start=start, end=end
        )
        currency_out, others = None, None

    comparison_out = None
    if comparison_window is not None:
        if history_from is not None and comparison_window.from_ < history_from:
            comparison_out = ComparisonOut(state="insufficient_history")
        else:
            cstart, cend = _utc_window(comparison_window, timezone)
            if key in _DEAL_FLOW_METRICS:
                previous, _cur, _others = await _compute_deal_flow(
                    session,
                    gate,
                    key,
                    organization_id=organization_id,
                    org_currency=org_currency,
                    start=cstart,
                    end=cend,
                )
            else:
                previous = await _compute_lead_flow(
                    session, gate, key, organization_id=organization_id, start=cstart, end=cend
                )
            if value is None or previous is None:
                # `win_rate` with nothing decided in one of the two windows — no rate exists
                # to compare, so no comparison is offered rather than a fabricated delta.
                comparison_out = None
            elif previous == 0:
                comparison_out = ComparisonOut(state="no_baseline", value=previous, delta_pct=None)
            elif spec.kind == "percent":
                comparison_out = ComparisonOut(
                    state="ok", value=previous, delta_pp=domain.delta_pp(value, previous)
                )
            else:
                comparison_out = ComparisonOut(
                    state="ok", value=previous, delta_pct=domain.delta_pct(value, previous)
                )

    return MetricOut(
        state="ok",
        kind=spec.kind,
        unit=spec.unit,
        scope=scope_label,
        value=value,
        currency=currency_out,
        other_currencies=others,
        comparison=comparison_out,
        history_from=history_from,
    )


async def build_summary(
    session: AsyncSession,
    principal: Principal,
    *,
    metric_keys: list[str],
    period: str,
    compare: str,
    from_: date | None,
    to: date | None,
) -> SummaryOut:
    """`GET /summary` (`docs/insights/api.md` §3). Raises `domain.InvalidPeriodError` for a
    malformed `period`/`compare`/`from`/`to` combination — the route turns that into a 422.
    """
    profile = await get_org_profile(session, principal.organization_id)
    timezone = org_timezone(profile)
    currency = org_currency(profile)
    fiscal_year_start_month = org_fiscal_year_start_month(profile)
    today = local_today(timezone)

    resolved = domain.resolve_period(
        period, today=today, from_=from_, to=to, fiscal_year_start_month=fiscal_year_start_month
    )
    comparison_window = domain.comparison_window(
        resolved, compare, fiscal_year_start_month=fiscal_year_start_month
    )
    comparison_out = (
        _period_out(compare, comparison_window) if comparison_window is not None else None
    )

    metrics = {
        key: await _summary_metric(
            session,
            principal,
            key,
            timezone=timezone,
            org_currency=currency,
            window=resolved.window,
            comparison_window=comparison_window,
        )
        for key in metric_keys
    }

    return SummaryOut(
        as_of=datetime.now(UTC),
        timezone=timezone,
        currency=currency,
        period=_period_out(resolved.key, resolved.window),
        comparison=comparison_out,
        metrics=metrics,
    )


async def build_series(
    session: AsyncSession,
    principal: Principal,
    *,
    metric: str,
    grain: str,
    from_: date,
    to: date,
) -> SeriesOut:
    """`GET /series` (`docs/insights/api.md` §4). `metric` must already be validated as a known,
    non-`"current"`-kind key — the route checks both before calling this."""
    spec = access.METRICS[metric]
    profile = await get_org_profile(session, principal.organization_id)
    timezone = org_timezone(profile)
    today = local_today(timezone)
    domain.validate_series_window(grain, from_, to, today=today)

    gate = access.gate_metric(principal, metric)
    now = datetime.now(UTC)
    if gate.state == "not_active":
        return SeriesOut(
            as_of=now,
            timezone=timezone,
            metric=metric,
            state="not_active",
            grain=grain,
            product_key=gate.product_key,  # type: ignore[arg-type]
        )
    if gate.state == "no_access":
        return SeriesOut(
            as_of=now, timezone=timezone, metric=metric, state="no_access", grain=grain
        )  # type: ignore[arg-type]
    return SeriesOut(
        as_of=now,
        timezone=timezone,
        metric=metric,
        state="unavailable",
        grain=grain,  # type: ignore[arg-type]
        scope=_scope_label(gate),
        unit=spec.unit,
    )


async def build_rankings(
    session: AsyncSession,
    principal: Principal,
    *,
    board: str,
    period: str,
    from_: date | None,
    to: date | None,
) -> RankingsOut:
    """`GET /rankings` (`docs/insights/api.md` §5)."""
    profile = await get_org_profile(session, principal.organization_id)
    currency = org_currency(profile)
    fiscal_year_start_month = org_fiscal_year_start_month(profile)
    today = local_today(org_timezone(profile))
    resolved = domain.resolve_period(
        period, today=today, from_=from_, to=to, fiscal_year_start_month=fiscal_year_start_month
    )

    gate = access.gate_board(principal, board)
    now = datetime.now(UTC)
    if gate.state == "not_active":
        return RankingsOut(as_of=now, board=board, state="not_active", product_key=gate.product_key)
    if gate.state == "no_access":
        return RankingsOut(as_of=now, board=board, state="no_access")
    return RankingsOut(
        as_of=now,
        board=board,
        state="unavailable",
        scope=_scope_label(gate),
        period=_period_out(resolved.key, resolved.window),
        currency=currency,
    )


def visible_activity_kinds(principal: Principal, products: set[str] | None) -> list[str]:
    """Every activity kind this caller's gate admits, optionally narrowed to `products` — the
    list `app/api/routes.py`'s `/activity` unions over once a fact table exists to read
    (`docs/insights/metrics.md` §6). Kept real in I1: it is pure gate logic, no I/O, and I2+'s
    activity query is built directly on top of exactly this list."""
    kinds = []
    for kind, spec in access.ACTIVITY_KINDS.items():
        if products is not None and spec.product_key not in products:
            continue
        if access.gate_activity_kind(principal, kind).state == "ok":
            kinds.append(kind)
    return kinds
