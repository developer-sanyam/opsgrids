"""The widget gate — ADR-0020 §4, `docs/insights/metrics.md` §4-§6.

**The chokepoint.** Every metric key, ranking board and activity kind is gated by the same
entitlement and permission that guard the source route for the thing it counts — the
`itsm/overview` pattern (one read gate per counted thing), evaluated **inside** this service
because a single `/summary` call can span every product the caller holds. This module is the
one place that catalogue lives; `app/selectors.py` calls `gate_metric`/`gate_board`/
`gate_activity_kind` and never reads `principal.entitlements`/`principal.perms` directly.

The gate never raises and never touches the database — it is a pure function of the forwarded
`Principal` and this module's own tables. `no_access` and `not_active` are **200s inside the
envelope**, not 403s: one card's denial must not blank the page, and a caller learns nothing
from it they do not already know about their own grants (ADR-0020 §4).

TEAM and LOCATION **narrow to OWN, never widen** — insights has no team or location column on
any fact, so a caller holding either is treated exactly as an OWN caller and the response says
`scope: "OWN"` (the honest caption, "Your deals" rather than "All deals").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from platform_core.auth import Principal, Scope

GateState = Literal["ok", "not_active", "no_access"]

MetricKind = Literal["flow", "state", "percent", "current"]
MetricUnit = Literal["money", "count", "percent"]
#: `docs/insights/metrics.md` §3 — which rule computes `metric_coverage.history_from` for a
#: metric once its fact table exists. Carried here, not executed here: I1 has no fact table for
#: any metric, so nothing yet reads this beyond documenting the slice that will.
CoverageBasis = Literal["source_dates", "source_column_from_go_live", "first_capture"]


@dataclass(frozen=True, slots=True)
class Gate:
    """The resolved access decision for one widget, on one request.

    `scope`/`subject_ref` are only meaningful when `state == "ok"` — `app/selectors.py` (I2+)
    passes them straight to `ScopeContext`-shaped filtering the same way every other service's
    `require(...)` dependency does, just resolved by hand instead of by FastAPI's DI, because
    the permission to check is not known until the request's own `metrics=`/`board=` parameter
    is parsed.
    """

    state: GateState
    scope: Scope | None = None
    subject_ref: uuid.UUID | None = None
    #: The entitlement key a `not_active` widget is missing — the SPA's own `PRODUCT_HOSTS`-style
    #: map turns this into "{Product} isn't active" for a holder of the activation permission
    #: (ADR-0020 §4) and hides the widget for everyone else.
    product_key: str | None = None


@dataclass(frozen=True, slots=True)
class MetricSpec:
    key: str
    label: str
    kind: MetricKind
    unit: MetricUnit
    #: The entitlement claim (`Principal.entitlements`) this metric's product activates under.
    product_key: str
    #: The permission that guards the source route for the thing this metric counts.
    permission: str
    #: Whether an OWN-scoped caller sees their own slice (`ScopeContext`'s `owner_col`) rather
    #: than being refused. ORG-only metrics (state metrics, and every finance one) are never
    #: own-capable — an OWN grant on one of those gets `no_access` (ADR-0020 §4).
    own_capable: bool
    coverage_basis: CoverageBasis


@dataclass(frozen=True, slots=True)
class BoardSpec:
    board: str
    product_key: str
    permission: str
    own_capable: bool


@dataclass(frozen=True, slots=True)
class ActivityKindSpec:
    kind: str
    product_key: str
    permission: str
    own_capable: bool


def _spec(
    key: str,
    label: str,
    kind: MetricKind,
    unit: MetricUnit,
    product_key: str,
    permission: str,
    *,
    own_capable: bool,
    coverage_basis: CoverageBasis,
) -> MetricSpec:
    return MetricSpec(
        key=key,
        label=label,
        kind=kind,
        unit=unit,
        product_key=product_key,
        permission=permission,
        own_capable=own_capable,
        coverage_basis=coverage_basis,
    )


#: The full metric catalogue (`docs/insights/metrics.md` §4) — 30 keys across 6 products, all
#: registered in I1 so the API contract is complete for frontend integration from the first
#: release, even though no fact table exists yet for any of them (`app/selectors.py` returns
#: `unavailable` for every one that passes its gate — see that module's own docstring). A later
#: slice never adds a *new* key here without also shipping the fact table and the fold that
#: back it; it only replaces `unavailable` with a real value.
METRICS: dict[str, MetricSpec] = {
    spec.key: spec
    for spec in (
        # -- finance (I2) ------------------------------------------------------------
        _spec(
            "revenue",
            "Revenue",
            "flow",
            "money",
            "finance",
            "finance.reports.summary.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        _spec(
            "invoices_issued",
            "Invoices issued",
            "flow",
            "count",
            "finance",
            "finance.billing.invoice.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        _spec(
            "payments_received",
            "Received",
            "flow",
            "money",
            "finance",
            "finance.billing.payment.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        _spec(
            "purchases_recorded",
            "Purchases recorded",
            "flow",
            "money",
            "finance",
            "finance.billing.purchase.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        # -- crm: deal (I3) ------------------------------------------------------------
        _spec(
            "deals_created",
            "Deals opened",
            "flow",
            "count",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "deals_won",
            "Deals won",
            "flow",
            "count",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=True,
            coverage_basis="source_column_from_go_live",
        ),
        _spec(
            "deals_won_value",
            "Won value",
            "flow",
            "money",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=True,
            coverage_basis="source_column_from_go_live",
        ),
        _spec(
            "deals_lost",
            "Deals lost",
            "flow",
            "count",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=True,
            coverage_basis="source_column_from_go_live",
        ),
        _spec(
            "win_rate",
            "Win rate",
            "percent",
            "percent",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=True,
            coverage_basis="source_column_from_go_live",
        ),
        _spec(
            "pipeline_value",
            "Open pipeline",
            "state",
            "money",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        _spec(
            "open_deals",
            "Open deals",
            "state",
            "count",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        _spec(
            "weighted_pipeline",
            "Weighted pipeline",
            "state",
            "money",
            "crm",
            "crm.pipeline.deal.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        # -- crm: lead + customer (I4) --------------------------------------------------
        _spec(
            "leads_created",
            "New leads",
            "flow",
            "count",
            "crm",
            "crm.lead.lead.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "leads_converted",
            "Leads converted",
            "flow",
            "count",
            "crm",
            "crm.lead.lead.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "customers",
            "Customers",
            "state",
            "count",
            "crm",
            "crm.book.company.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        # -- operations (I5) -------------------------------------------------------------
        _spec(
            "purchase_orders_raised",
            "POs raised",
            "flow",
            "count",
            "operations",
            "operations.procurement.purchase_order.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "purchase_orders_raised_value",
            "PO value",
            "flow",
            "money",
            "operations",
            "operations.procurement.purchase_order.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "on_time_delivery",
            "On-time delivery",
            "percent",
            "percent",
            "operations",
            "operations.procurement.purchase_order.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "stock_value",
            "Stock value",
            "state",
            "money",
            "operations",
            "operations.inventory.item.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        _spec(
            "low_stock_items",
            "Low stock",
            "state",
            "count",
            "operations",
            "operations.inventory.item.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        _spec(
            "out_of_stock_items",
            "Out of stock",
            "state",
            "count",
            "operations",
            "operations.inventory.item.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        # -- projects (I6) -----------------------------------------------------------------
        _spec(
            "tasks_created",
            "Tasks added",
            "flow",
            "count",
            "projects",
            "projects.board.task.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        _spec(
            "tasks_completed",
            "Tasks completed",
            "flow",
            "count",
            "projects",
            "projects.board.task.read",
            own_capable=True,
            coverage_basis="source_column_from_go_live",
        ),
        _spec(
            "task_points_completed",
            "Points completed",
            "flow",
            "count",
            "projects",
            "projects.board.task.read",
            own_capable=True,
            coverage_basis="source_column_from_go_live",
        ),
        _spec(
            "open_tasks",
            "Open tasks",
            "state",
            "count",
            "projects",
            "projects.board.task.read",
            own_capable=False,
            coverage_basis="first_capture",
        ),
        _spec(
            "overdue_tasks",
            "Overdue tasks",
            "current",
            "count",
            "projects",
            "projects.board.task.read",
            own_capable=True,
            coverage_basis="source_dates",
        ),
        # -- storefront (I7) ---------------------------------------------------------------
        _spec(
            "enquiries_received",
            "Enquiries",
            "flow",
            "count",
            "storefront",
            "storefront.sales.enquiry.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        # -- hr (I9, optional) --------------------------------------------------------------
        _spec(
            "headcount",
            "Headcount",
            "state",
            "count",
            "hrms",
            "hrms.people.employee.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        _spec(
            "checked_in",
            "Checked in",
            "flow",
            "count",
            "hrms",
            "hrms.time.attendance.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
        _spec(
            "on_leave",
            "On leave",
            "flow",
            "count",
            "hrms",
            "hrms.time.leave_request.read",
            own_capable=False,
            coverage_basis="source_dates",
        ),
    )
}

#: Team performance and most-enquired products (`docs/insights/metrics.md` §5).
BOARDS: dict[str, BoardSpec] = {
    board.board: board
    for board in (
        BoardSpec("sales_reps", "crm", "crm.pipeline.deal.read", own_capable=True),
        BoardSpec("task_assignees", "projects", "projects.board.task.read", own_capable=True),
        BoardSpec(
            "enquired_products", "storefront", "storefront.sales.enquiry.read", own_capable=False
        ),
    )
}

#: Recent activity kinds (`docs/insights/metrics.md` §6), each gated on the owner column named
#: there — `mine` on a returned item is `owner_ref == subject_ref`, computed by the selector,
#: not by this module.
ACTIVITY_KINDS: dict[str, ActivityKindSpec] = {
    activity.kind: activity
    for activity in (
        ActivityKindSpec(
            "invoice_issued", "finance", "finance.billing.invoice.read", own_capable=False
        ),
        ActivityKindSpec(
            "credit_note_issued", "finance", "finance.billing.invoice.read", own_capable=False
        ),
        ActivityKindSpec(
            "invoice_paid", "finance", "finance.billing.invoice.read", own_capable=False
        ),
        ActivityKindSpec(
            "payment_received", "finance", "finance.billing.payment.read", own_capable=False
        ),
        ActivityKindSpec("lead_created", "crm", "crm.lead.lead.read", own_capable=True),
        ActivityKindSpec("deal_created", "crm", "crm.pipeline.deal.read", own_capable=True),
        ActivityKindSpec("deal_won", "crm", "crm.pipeline.deal.read", own_capable=True),
        ActivityKindSpec("deal_lost", "crm", "crm.pipeline.deal.read", own_capable=True),
        ActivityKindSpec("company_created", "crm", "crm.book.company.read", own_capable=True),
        ActivityKindSpec(
            "purchase_order_raised",
            "operations",
            "operations.procurement.purchase_order.read",
            own_capable=True,
        ),
        ActivityKindSpec(
            "task_completed", "projects", "projects.board.task.read", own_capable=True
        ),
        ActivityKindSpec(
            "enquiry_received", "storefront", "storefront.sales.enquiry.read", own_capable=False
        ),
    )
}


def _resolve(principal: Principal, *, product_key: str, permission: str, own_capable: bool) -> Gate:
    """The one gate rule (ADR-0020 §4), shared by every widget kind:

    1. entitlement missing -> `not_active`;
    2. permission missing -> `no_access`;
    3. TEAM/LOCATION narrow to OWN;
    4. an OWN scope on a widget that is not own-capable -> `no_access` (an ORG-only widget
       admits ORG and nothing narrower);
    5. otherwise `ok`, at the resolved scope.
    """
    if not principal.has_product(product_key):
        return Gate(state="not_active", product_key=product_key)
    scope = principal.scope_for(permission)
    if scope is None:
        return Gate(state="no_access")
    if scope in (Scope.TEAM, Scope.LOCATION):
        scope = Scope.OWN
    if scope is Scope.OWN and not own_capable:
        return Gate(state="no_access")
    subject_ref = principal.subject_ref if scope is Scope.OWN else None
    return Gate(state="ok", scope=scope, subject_ref=subject_ref)


def gate_metric(principal: Principal, key: str) -> Gate:
    spec = METRICS[key]
    return _resolve(
        principal,
        product_key=spec.product_key,
        permission=spec.permission,
        own_capable=spec.own_capable,
    )


def gate_board(principal: Principal, board: str) -> Gate:
    spec = BOARDS[board]
    return _resolve(
        principal,
        product_key=spec.product_key,
        permission=spec.permission,
        own_capable=spec.own_capable,
    )


def gate_activity_kind(principal: Principal, kind: str) -> Gate:
    spec = ACTIVITY_KINDS[kind]
    return _resolve(
        principal,
        product_key=spec.product_key,
        permission=spec.permission,
        own_capable=spec.own_capable,
    )
