"""Request and response contracts for insights (`docs/insights/api.md`).

This module is the OpenAPI surface. I1 ships the full response shape every endpoint commits to
— `docs/insights/api.md`'s own examples — so the frontend can integrate against it immediately
(ADR-0020 §12: "I1 ... The API contract is live for frontend integration: every widget renders
its state. No figures yet."). What I1 cannot yet produce is a `state: "ok"` metric, series point
or ranking row: no entity fact table exists before I2. Every field that only makes sense once a
widget is `"ok"` is therefore optional, and I1's own selectors never set it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: `docs/insights/api.md` §1. `insufficient_history` and `unavailable` both render as "nothing
#: for this widget"; the SPA tells them apart only for its retry affordance.
WidgetState = Literal["ok", "not_active", "no_access", "insufficient_history", "unavailable"]
Scope = Literal["ORG", "OWN"]
MetricKind = Literal["flow", "state", "percent", "current"]
MetricUnit = Literal["money", "count", "percent"]
ComparisonState = Literal["ok", "no_baseline", "insufficient_history"]


class PeriodOut(BaseModel):
    """A resolved window, echoed back so the SPA never computes a date itself
    (`docs/insights/api.md` §2)."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    from_date: date = Field(alias="from")
    to: date
    label: str


class OtherCurrencyValueOut(BaseModel):
    currency: str
    value: int


class ComparisonOut(BaseModel):
    state: ComparisonState
    value: int | float | None = None
    delta_pct: float | None = None
    delta_pp: float | None = None
    #: Only set when the comparison fell back to the nearest earlier capture
    #: (`docs/insights/metrics.md` §2).
    anchor_day: date | None = None


class MetricOut(BaseModel):
    """One entry of `SummaryOut.metrics` (`docs/insights/api.md` §3).

    Every field below `state` is `None` unless the widget is `"ok"` — a `no_access` or
    `not_active` entry carries only `state` (and, for `not_active`, `product_key`), exactly as
    `docs/insights/api.md`'s own `"customers": {"state": "no_access"}` example shows.
    """

    state: WidgetState
    kind: MetricKind | None = None
    unit: MetricUnit | None = None
    scope: Scope | None = None
    value: int | float | None = None
    currency: str | None = None
    other_currencies: list[OtherCurrencyValueOut] | None = None
    #: Set only for a pulled state metric (`stock_value`) — when its own capture was taken.
    as_of: datetime | None = None
    comparison: ComparisonOut | None = None
    history_from: date | None = None
    #: Set only when `state == "not_active"` — the entitlement key the widget is missing.
    product_key: str | None = None


class SummaryOut(BaseModel):
    as_of: datetime
    timezone: str
    currency: str
    period: PeriodOut
    comparison: PeriodOut | None = None
    metrics: dict[str, MetricOut]


class SeriesPointOut(BaseModel):
    start: date
    end: date
    value: int | float | None = None
    #: Only present with `breakdown=source` (`leads_created`) — replaces `value`.
    values: dict[str, int] | None = None
    #: The bucket containing "today" is incomplete; every other covered bucket is `True`.
    complete: bool = True


class OtherCurrencySeriesOut(BaseModel):
    currency: str
    points: list[SeriesPointOut]


class SeriesOut(BaseModel):
    as_of: datetime
    timezone: str
    metric: str
    state: WidgetState
    scope: Scope | None = None
    unit: MetricUnit | None = None
    currency: str | None = None
    grain: Literal["day", "week", "month"]
    history_from: date | None = None
    points: list[SeriesPointOut] = Field(default_factory=list)
    other_currencies: list[OtherCurrencySeriesOut] = Field(default_factory=list)
    #: Set only when `state == "not_active"`.
    product_key: str | None = None


class ActivitySubjectOut(BaseModel):
    type: str
    id: str
    label: str | None = None


class ActivityAmountOut(BaseModel):
    value: int
    currency: str


class ActivityItemOut(BaseModel):
    id: str
    occurred_at: datetime
    product_key: str
    kind: str
    subject: ActivitySubjectOut
    amount: ActivityAmountOut | None = None
    mine: bool


class ActivityOut(BaseModel):
    as_of: datetime
    items: list[ActivityItemOut] = Field(default_factory=list)
    next_cursor: str | None = None


class RankingRowOut(BaseModel):
    #: Present for `sales_reps`/`task_assignees`; absent for `enquired_products`, which carries
    #: `product_ref`/`label` instead (`docs/insights/api.md` §5).
    person_ref: str | None = None
    product_ref: str | None = None
    label: str | None = None
    values: dict[str, int]


class RankingsOut(BaseModel):
    as_of: datetime
    board: str
    state: WidgetState
    scope: Scope | None = None
    period: PeriodOut | None = None
    currency: str | None = None
    rows: list[RankingRowOut] = Field(default_factory=list)
    #: Set only when `state == "not_active"`.
    product_key: str | None = None
