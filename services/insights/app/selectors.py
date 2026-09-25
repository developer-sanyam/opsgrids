"""Reads for insights. Visibility rules live here, in one place (ADR-0020 §4, §7).

Every widget goes through `app/access.py:gate_*` before this module ever builds a query — a
`not_active`/`no_access` gate short-circuits before touching the database, matching
"authorization is server-side" (CLAUDE.md). `scoped()` is the chokepoint every I2+ fact query
starts from: it filters `organization_id` explicitly (RLS is the net, not the filter) and then,
for an OWN-scoped gate, narrows to the caller's own rows — the same rule
`platform_core.auth.ScopeContext.apply` enforces for every other service's list endpoint.

I1 has no entity fact table, so every widget whose gate resolves to `"ok"` reads as
`"unavailable"` here — there is nothing yet to compute (ADR-0020 build plan, I1: "every widget
renders its state. No figures yet."). A slice that lands a fact table replaces exactly the
`"unavailable"` branch of its own metric/board/activity kind with a real read; the gate, the
scoping and the response shape do not change.

I2 replaces that branch for `revenue`, `invoices_issued`, `payments_received` and
`purchases_recorded` (`docs/insights/metrics.md` §4.1) with real reads over `invoice_facts`,
`payment_facts` and `purchase_facts`. All four are `source_dates` coverage — complete from day
one (`metrics.md` §3) — so none of them ever consults `metric_coverage`; `/series`, `/activity`
and `/rankings` for finance are unchanged, still `"unavailable"`, pending a later pass.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, select
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, domain
from app.models import InvoiceFacts, OrgProfile, PaymentFacts, PurchaseFacts
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


def _window_filter(column: Any, window: domain.Window) -> Any:
    return column.between(window.from_, window.to)


async def _money_by_currency(
    session: AsyncSession, stmt: Select[tuple[str, int]]
) -> dict[str, int]:
    """`GROUP BY currency`, dropped to a plain dict — every currency's group is summed
    independently and never added into another (`docs/insights/metrics.md` §1: "Money is summed
    per currency")."""
    rows = (await session.execute(stmt)).all()
    return {currency: int(value) for currency, value in rows if value}


#: `docs/insights/metrics.md` §4.1: revenue is net of credit notes (they are negative), and
#: excludes `draft`/`void` documents.
_REVENUE_DOCUMENT_TYPES = ("invoice", "credit_note")
_EXCLUDED_INVOICE_STATUSES = ("draft", "void")

#: `purchases_recorded` / "Expenses": Σ `total_minor` signed by `document_type` — a credit note
#: reduces spend, a debit note or an ordinary invoice adds to it (`docs/insights/metrics.md` §4.1).
_PURCHASE_SIGN = case((PurchaseFacts.document_type == "credit_note", -1), else_=1)


async def _revenue_by_currency(
    session: AsyncSession, organization_id: uuid.UUID, window: domain.Window
) -> dict[str, int]:
    stmt = (
        select(InvoiceFacts.currency, func.coalesce(func.sum(InvoiceFacts.total_minor), 0))
        .where(
            InvoiceFacts.organization_id == organization_id,
            InvoiceFacts.deleted_at.is_(None),
            InvoiceFacts.document_type.in_(_REVENUE_DOCUMENT_TYPES),
            InvoiceFacts.status.notin_(_EXCLUDED_INVOICE_STATUSES),
            _window_filter(InvoiceFacts.issued_date, window),
        )
        .group_by(InvoiceFacts.currency)
    )
    return await _money_by_currency(session, stmt)


async def _invoices_issued_count(
    session: AsyncSession, organization_id: uuid.UUID, window: domain.Window
) -> int:
    stmt = (
        select(func.count())
        .select_from(InvoiceFacts)
        .where(
            InvoiceFacts.organization_id == organization_id,
            InvoiceFacts.deleted_at.is_(None),
            InvoiceFacts.document_type == "invoice",
            InvoiceFacts.status.notin_(_EXCLUDED_INVOICE_STATUSES),
            _window_filter(InvoiceFacts.issued_date, window),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def _payments_received_by_currency(
    session: AsyncSession, organization_id: uuid.UUID, window: domain.Window
) -> dict[str, int]:
    stmt = (
        select(PaymentFacts.currency, func.coalesce(func.sum(PaymentFacts.amount_minor), 0))
        .where(
            PaymentFacts.organization_id == organization_id,
            PaymentFacts.deleted_at.is_(None),
            _window_filter(PaymentFacts.received_date, window),
        )
        .group_by(PaymentFacts.currency)
    )
    return await _money_by_currency(session, stmt)


async def _purchases_recorded_by_currency(
    session: AsyncSession, organization_id: uuid.UUID, window: domain.Window
) -> dict[str, int]:
    stmt = (
        select(
            PurchaseFacts.currency,
            func.coalesce(func.sum(PurchaseFacts.total_minor * _PURCHASE_SIGN), 0),
        )
        .where(
            PurchaseFacts.organization_id == organization_id,
            PurchaseFacts.deleted_at.is_(None),
            _window_filter(PurchaseFacts.document_date, window),
        )
        .group_by(PurchaseFacts.currency)
    )
    return await _money_by_currency(session, stmt)


#: The four finance flow metrics I2 lands (`docs/insights/metrics.md` §4.1). Every one is
#: `source_dates` coverage — `NULL`/complete from day one (§3) — so, unlike a snapshot or a
#: source-column metric, none of these ever consults `metric_coverage`: there is nothing before
#: which their value or comparison could be `insufficient_history`.
_FINANCE_MONEY_METRICS: dict[str, Any] = {
    "revenue": _revenue_by_currency,
    "payments_received": _payments_received_by_currency,
    "purchases_recorded": _purchases_recorded_by_currency,
}


def _money_metric_out(
    spec: access.MetricSpec,
    scope_label: str,
    org_currency_code: str,
    by_currency: dict[str, int],
    previous_by_currency: dict[str, int] | None,
) -> MetricOut:
    value = by_currency.get(org_currency_code, 0)
    others = [
        OtherCurrencyValueOut(currency=code, value=amount)
        for code, amount in sorted(by_currency.items())
        if code != org_currency_code and amount != 0
    ]
    comparison = None
    if previous_by_currency is not None:
        previous_value = previous_by_currency.get(org_currency_code, 0)
        comparison = ComparisonOut(
            state="ok" if previous_value != 0 else "no_baseline",
            value=previous_value,
            delta_pct=domain.delta_pct(value, previous_value),
        )
    return MetricOut(
        state="ok",
        kind=spec.kind,
        unit=spec.unit,
        scope=scope_label,
        value=value,
        currency=org_currency_code,
        other_currencies=others,
        comparison=comparison,
        history_from=None,
    )


async def _summary_metric(
    session: AsyncSession,
    principal: Principal,
    key: str,
    *,
    window: domain.Window,
    comparison_window: domain.Window | None,
    org_currency_code: str,
) -> MetricOut:
    """One entry of `GET /summary`'s `metrics` map. `key` is assumed already validated against
    `app.access.METRICS` — an unknown key is a 422 at the route, never reaches here."""
    spec = access.METRICS[key]
    gate = access.gate_metric(principal, key)
    if gate.state == "not_active":
        return MetricOut(state="not_active", product_key=gate.product_key)
    if gate.state == "no_access":
        return MetricOut(state="no_access")
    scope_label = _scope_label(gate)
    organization_id = principal.organization_id

    if key in _FINANCE_MONEY_METRICS:
        by_currency = await _FINANCE_MONEY_METRICS[key](session, organization_id, window)
        previous_by_currency = (
            await _FINANCE_MONEY_METRICS[key](session, organization_id, comparison_window)
            if comparison_window is not None
            else None
        )
        return _money_metric_out(
            spec, scope_label, org_currency_code, by_currency, previous_by_currency
        )

    if key == "invoices_issued":
        value = await _invoices_issued_count(session, organization_id, window)
        comparison = None
        if comparison_window is not None:
            previous_value = await _invoices_issued_count(
                session, organization_id, comparison_window
            )
            comparison = ComparisonOut(
                state="ok" if previous_value != 0 else "no_baseline",
                value=previous_value,
                delta_pct=domain.delta_pct(value, previous_value),
            )
        return MetricOut(
            state="ok",
            kind=spec.kind,
            unit=spec.unit,
            scope=scope_label,
            value=value,
            comparison=comparison,
            history_from=None,
        )

    # No fact table exists yet for this key (I3-I9). An entitled, permitted caller sees an
    # honest "not computed yet", never a fabricated zero.
    return MetricOut(state="unavailable", kind=spec.kind, unit=spec.unit, scope=scope_label)


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

    metrics: dict[str, MetricOut] = {}
    for key in metric_keys:
        metrics[key] = await _summary_metric(
            session,
            principal,
            key,
            window=resolved.window,
            comparison_window=comparison_window,
            org_currency_code=currency,
        )

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
