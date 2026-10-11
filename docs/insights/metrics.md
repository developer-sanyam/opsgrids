# Insights: metric definitions

Part of [ADR-0020](../adr/0020-insights-projection.md). Every figure the dashboards take from
insights is defined here, once. A definition that changes is a change to this file in the same
commit as the code, and the parity tests in §6 hold the definitions that duplicate a source's to
that source.

## 1. Conventions

- **Local day.** `tz` is the organization's timezone (`org_profile.timezone`, Asia/Kolkata by
  default). `today` is `(now() AT TIME ZONE tz)::date`, computed on every request.
- **Windows** are inclusive local dates `[from, to]`:
  - A **date** column (`issued_date`, `received_date`, …) is in the window when it lies between
    `from` and `to`.
  - A **timestamp** column is in the window when it lies in
    `[from 00:00 tz, (to + 1 day) 00:00 tz)`, converted to UTC instants so the org-leading index
    is used.
- **Filters every definition carries.** `organization_id = :org` and `deleted_at IS NULL`, plus
  the OWN filter `owner_ref = :subject_ref` when the caller's scope is OWN (§4). These are not
  repeated below.
- **Money** is summed **per currency**:
  - `value` is the organization-currency group, and the other currencies come back in
    `other_currencies`, never added in;
  - a currency's group is omitted when its sum is 0.
- **Rounding.**
  - Percentages are returned with one decimal (`87.5`).
  - `delta_pct` is `round((current − previous) / |previous| × 100, 1)`.
  - `delta_pp` (for percentage metrics) is `round(current − previous, 1)`.
  - Rounding is half-even everywhere, to match Python's `round`, which the sources use.
- **Kinds.**
  - **flow**: summed over a window.
  - **state**: a level at an instant. Its value is at the window's end, and its history comes
    from snapshots or from dates.
  - **percent**: a ratio over a window.
  - **current**: now only, with no comparison and no series.

## 2. Periods and comparisons

`period` values, all in the organization's timezone:

| `period` | Window | Previous period (`compare=previous_period`, the default) |
|---|---|---|
| `today` | `[today, today]` | `[today−1, today−1]` |
| `this_week` | `[Monday of this ISO week, today]` | The same elapsed span from last week's Monday |
| `this_month` (default) | `[1st of this month, today]` | The same elapsed span from the 1st of last month, clipped to last month's length: **1–25 Sep → 1–25 Aug; 1–31 Mar → 1–28 Feb** |
| `this_quarter` | `[start of this fiscal quarter, today]`. Fiscal quarters start at `fiscal_year_start_month` (April by default: Apr–Jun, Jul–Sep, Oct–Dec, Jan–Mar). | The same elapsed span from the previous fiscal quarter's start, clipped to its length |
| `last_30_days` | `[today−29, today]` | `[today−59, today−30]` |
| `last_month` | The previous calendar month | The calendar month before it |
| `custom` | `[from, to]`, with `from ≤ to ≤ today` and at most 366 days | If the window is exactly whole calendar months, the same number of whole months immediately before. Otherwise the same number of days immediately before `from`. |

- `compare=same_period_last_year` shifts both ends back one year (29 Feb becomes 28 Feb).
- `compare=none` omits the comparison.
- **Comparing a state metric.** The value is taken **at the end of the window**: live, or the
  latest capture, for a to-date window, and the capture of day `to` for a past window. The
  comparison value is taken **at the end of the comparison window**: on 25 September with
  `this_month`, now against the end of 25 August.
- **When a capture is missing** for an anchor day, the nearest earlier capture within 3 days is
  used, and its `day` is returned as `comparison.anchor_day`. Otherwise the comparison is
  `insufficient_history`.

## 3. Coverage: when a value or delta may be shown

`metric_coverage.history_from` is per organization and per metric. `NULL` means complete over the
source's whole history.

- A **value** whose window starts before `history_from` is not returned: its `state` is
  `insufficient_history`.
- A **comparison** whose window starts before `history_from` is not returned:
  `comparison.state` is `insufficient_history`.
- A **series point** whose bucket starts before `history_from` is omitted. It is not zero.
- `comparison.state = no_baseline` when the previous value is 0 (no "+∞%"), and `delta_pct` is
  then `null`.

| Basis | Metrics | `history_from` |
|---|---|---|
| `source_dates`: every row carries its own business date or creation time | `revenue`, `invoices_issued`, `payments_received`, `purchases_recorded`, `leads_created`, `leads_converted`, `deals_created`, `purchase_orders_raised*`, `on_time_delivery`, `tasks_created`, `enquiries_received`, `headcount`, `checked_in`, `on_leave` | `NULL` (complete) |
| `source_column_from_go_live`: complete only if the owning service's migration found a date for every closed row | `deals_won`, `deals_won_value`, `deals_lost`, `win_rate` (from `deals.closed_at`); `tasks_completed`, `task_points_completed` (from `tasks.completed_at`) | `NULL` if no closed deal / done task of the organization has a null date after the migration, else the go-live date |
| `first_capture`: history is recorded, not derivable | `pipeline_value`, `open_deals`, `weighted_pipeline`, `customers`, `open_tasks`, `stock_value`, `low_stock_items`, `out_of_stock_items` | The organization's first snapshot day |

## 4. The catalogue

**Gate** = entitlement (`ent` claim) + permission + minimum scope. "OWN-capable" means an OWN
caller gets the metric filtered on the named `owner_ref` column. TEAM and LOCATION narrow to OWN.
"ORG only" means anything below ORG gets `no_access`. Every gate below is the gate of the source
route that lists or summarises the counted thing, or narrower.

### 4.1 Finance (entitlement `finance`; slice I2)

| Key | UI label | Kind · unit | Definition | Gate |
|---|---|---|---|---|
| `revenue` | Revenue | flow · money | Σ `total_minor` of `invoice_facts` where `document_type IN ('invoice','credit_note')`, `status NOT IN ('draft','void')`, and `issued_date` in window. Credit notes are negative, so this is net of credit notes. **Includes GST** (ADR Q1). Identical to `finance.domain.net_revenue_for_month` for a whole month. | `finance.reports.summary.read`, ORG only |
| `invoices_issued` | Invoices issued | flow · count | Count of `invoice_facts` where `document_type = 'invoice'`, `status NOT IN ('draft','void')`, and `issued_date` in window. The honest stand-in for "Orders". | `finance.billing.invoice.read`, ORG only |
| `payments_received` | Received | flow · money | Σ `amount_minor` of `payment_facts` with `received_date` in window. "Money in" on the Finance dashboard. | `finance.billing.payment.read`, ORG only |
| `purchases_recorded` | Purchases recorded | flow · money | Σ of `total_minor` × (−1 for `credit_note`, +1 for `invoice`/`debit_note`) over `purchase_facts` with `document_date` in window. "Expenses" means exactly this, and the caption says so. | `finance.billing.purchase.read`, ORG only |

### 4.2 CRM (entitlement `crm`; slices I3, I4)

| Key | UI label | Kind · unit | Definition | Gate |
|---|---|---|---|---|
| `leads_created` | New leads | flow · count | Count of `lead_facts` with `source_created_at` in window. `breakdown=source` splits it by `form`/`import`/`manual`. | `crm.lead.lead.read`, OWN-capable (`leads.owner_id`) |
| `leads_converted` | Leads converted | flow · count | Count of `lead_facts` with `converted_at` in window | same |
| `deals_created` | Deals opened | flow · count | Count of `deal_facts` with `source_created_at` in window (a backdated import counts on its backdated date) | `crm.pipeline.deal.read`, OWN-capable (`deals.owner_id`) |
| `deals_won` | Deals won | flow · count | Count of `deal_facts` where `stage_outcome = 'won'` (set from the event's `to_outcome`) and `closed_at` in window. The "Orders" stand-in for Sales. | same |
| `deals_won_value` | Won value | flow · money | Σ `value_minor` over the same rows as `deals_won` | same |
| `deals_lost` | Deals lost | flow · count | Count where `stage_outcome = 'lost'` and `closed_at` in window | same |
| `win_rate` | Win rate | percent | `deals_won ÷ (deals_won + deals_lost) × 100` over the window; `null` when none were decided. **Not** `/deals/summary.win_rate_pct`, which is all-time; the caption names the period. Delta in percentage points. | same |
| `pipeline_value` | Open pipeline | state · money | Σ `value_minor` of `deal_facts` where `stage_outcome = 'open'`. Live now; history from snapshots. | `crm.pipeline.deal.read`, **ORG only** |
| `open_deals` | Open deals | state · count | Count over the same rows | same |
| `weighted_pipeline` | Weighted pipeline | state · money | Σ per deal of `round_half_even(value_minor × probability / 100)` over the same rows. That is `deal.domain.weighted_pipeline_minor`'s rule; Postgres `round(numeric)` rounds half away from zero, so this is computed in `app/domain.py`, not in SQL. | same |
| `customers` | Customers | state · count | Count of `company_facts` with `status = 'customer'` | `crm.book.company.read`, **ORG only** |

### 4.3 Operations (entitlement `operations`; slice I5)

| Key | UI label | Kind · unit | Definition | Gate |
|---|---|---|---|---|
| `purchase_orders_raised` | POs raised | flow · count | Count of `purchase_order_facts` where `status <> 'cancelled'` and `created_date` in window | `operations.procurement.purchase_order.read`, OWN-capable (`purchase_orders.raised_by_id`) |
| `purchase_orders_raised_value` | PO value | flow · money | Σ `total_minor` over the same rows | same |
| `on_time_delivery` | On-time delivery | percent | Of POs with `received_date` in window: `count(received_date <= expected_date) ÷ count(*) × 100`; `null` when none were received. It replaces the seeded `Supplier.on_time_pct`, which must not be rendered. Delta in percentage points. | same |
| `stock_value` | Stock value | state · money | Operations' own `inventory_value_minor` (Σ on-hand × unit value, rounded once per item, ADR-0014 §2), pulled hourly (§8). The value is the latest capture, and `as_of` is its `captured_at`. | `operations.inventory.item.read`, **ORG only**. Narrower than `/operations/overview`, which admits any PO reader: the gate is on the thing counted. |
| `low_stock_items` | Low stock | state · count | Operations' `low_stock_count` (at or below the reorder point), pulled | same |
| `out_of_stock_items` | Out of stock | state · count | Count of items with `on_hand_milli = 0`, pulled | same |

### 4.4 Projects (entitlement `projects`; slice I6)

| Key | UI label | Kind · unit | Definition | Gate |
|---|---|---|---|---|
| `tasks_created` | Tasks added | flow · count | Count of `task_facts` with `source_created_at` in window | `projects.board.task.read`, OWN-capable (`tasks.assignee_id`) |
| `tasks_completed` | Tasks completed | flow · count | Count where `stage = 'done'` and `completed_at` in window. The Burndown replacement is its `week` series. | same |
| `task_points_completed` | Points completed | flow · count | Σ `points` over the same rows | same |
| `open_tasks` | Open tasks | state · count | Count where `stage <> 'done'` | `projects.board.task.read`, **ORG only** |
| `overdue_tasks` | Overdue tasks | current · count | Count where `stage <> 'done'` and `due_date < today` | OWN-capable, as `tasks_created` |

### 4.5 Storefront (entitlement `storefront`; slice I7)

| Key | UI label | Kind · unit | Definition | Gate |
|---|---|---|---|---|
| `enquiries_received` | Enquiries | flow · count | Count of `enquiry_facts` with `source_created_at` in window. The "Orders" stand-in for a storefront business. | `storefront.sales.enquiry.read`, ORG only (the permission's own max) |

### 4.6 HR (entitlement `hrms`; slice I9, optional)

| Key | UI label | Kind · unit | Definition | Gate |
|---|---|---|---|---|
| `headcount` | Headcount | state · count, **date-derived** (not snapshotted) | As of day D: count of `employee_facts` where `COALESCE(joined_on, local_date(source_created_at)) <= D` and `(exited_on IS NULL OR exited_on > D)` and not (`status = 'exited'` with `exited_on IS NULL`) | `hrms.people.employee.read`, ORG only |
| `checked_in` | Checked in | flow · count | Day D: distinct `employee_ref` with an `attendance_facts` row on `work_date = D` and `status IN ('present','half_day','regularized')`. A longer window is the average per day over days with at least one record. No rate: there is no working-day calendar (ADR Q2). | `hrms.time.attendance.read`, ORG only |
| `on_leave` | On leave | flow · count | Day D: distinct `employee_ref` with an approved `leave_facts` row where `start_date <= D <= end_date`. A longer window is the average per day. | `hrms.time.leave_request.read`, ORG only |

## 5. Rankings (team performance, most-enquired products)

People are returned as the source's own id (`person_ref`). The SPA resolves names through the
product's directory read, which carries its own gate. Insights stores no names.

| Board | Rows | Values | Order | Gate |
|---|---|---|---|---|
| `sales_reps` | One per `deal_facts.owner_ref` with any activity in the window | `deals_won`, `deals_won_value` (org currency), `deals_created`, all as defined above, per owner | `deals_won_value` desc, then `deals_won` desc | `crm.pipeline.deal.read`. An OWN caller gets only their own row. Names come from `GET /customers/owners`. |
| `task_assignees` | One per `task_facts.owner_ref` | `tasks_completed`, `task_points_completed` (window), `open_tasks`, `overdue_tasks` (now), per assignee | `tasks_completed` desc | `projects.board.task.read`, with an OWN caller getting their own row. Names come from `GET /projects-workspace/members`. |
| `enquired_products` | One per `enquiry_line_facts.product_ref` over enquiries created in the window | `enquiries` (distinct enquiries naming it), `units` (Σ quantity), `label` (the most recent `product_name`) | `enquiries` desc, then `units` desc | `storefront.sales.enquiry.read`, ORG only |

`limit` defaults to 5, maximum 10. "Team workload" on the PM dashboard is `task_assignees` ordered
client-side by `open_tasks`.

## 6. Recent activity kinds

Each kind is a query over one fact table's own timestamp. It is included only when the caller
passes its gate, and filtered on its owner column for OWN.

| Kind | Product | Timestamp | Subject label | Amount | Gate (owner column) |
|---|---|---|---|---|---|
| `invoice_issued` | finance | `invoice_facts.source_created_at` (document_type invoice) | `number` | `total_minor` | `finance.billing.invoice.read` ORG |
| `credit_note_issued` | finance | the same (document_type credit_note) | `number` | `total_minor` (negative) | same |
| `invoice_paid` | finance | `invoice_facts.paid_at` | `number` | `total_minor` | same |
| `payment_received` | finance | `payment_facts.source_created_at` | `reference` | `amount_minor` | `finance.billing.payment.read` ORG |
| `lead_created` | crm | `lead_facts.source_created_at` | none (a lead's label is a person's name: shown as "New lead · {source}") | none | `crm.lead.lead.read` (`owner_ref`) |
| `deal_created` | crm | `deal_facts.source_created_at` | `name` | `value_minor` | `crm.pipeline.deal.read` (`owner_ref`) |
| `deal_won` | crm | `deal_facts.closed_at` (outcome `won`) | `name` | `value_minor` | same |
| `deal_lost` | crm | `deal_facts.closed_at` (outcome `lost`) | `name` | none | same |
| `company_created` | crm | `company_facts.source_created_at`. Worded "New customer" when `status = 'customer'`, else "New company". | `name` | none | `crm.book.company.read` (`owner_ref`) |
| `purchase_order_raised` | operations | `purchase_order_facts.source_created_at` | `number` · `supplier_name` | `total_minor` | `operations.procurement.purchase_order.read` (`owner_ref`) |
| `task_completed` | projects | `task_facts.completed_at` | `title` | none | `projects.board.task.read` (`owner_ref`) |
| `enquiry_received` | storefront | `enquiry_facts.source_created_at` | `company` | none | `storefront.sales.enquiry.read` ORG |

Each item carries `mine: true` when its owner column equals the caller's `subject_ref`, so a feed
row can read "you won …". There is no read or unread state: this is a feed, not an inbox.

## 7. Parity tests (definitions that duplicate a source's)

Each runs the source's own function and insights' query over the **same fixture rows** and
asserts equality. They live in insights' test suite, with the fixture built through the source's
real write path, as `services/approval/tests/test_cross_service_contract.py` does, taking the
schema lock.

| Insights | Source | Held equal for |
|---|---|---|
| `revenue` for a whole month | `finance.domain.net_revenue_for_month` | Every month in the fixture |
| `pipeline_value`, `open_deals`, `weighted_pipeline` | `deal.domain.pipeline_value_minor`, `open_count`, `weighted_pipeline_minor` | Single-currency organizations. **`/deals/summary` sums `value_minor` across currencies today**, so a multi-currency organization is where they legitimately differ. Insights is the correct one, and the deal-side fix is noted in `events.md` §5. |
| `customers` | `/customers?status=customer` `total` at ORG | Always |
| `stock_value`, `low_stock_items` | `/operations/overview` `inventory_value_minor`, `low_stock_count` | Always: insights stores what operations computed |

## 8. Snapshot capture

- The sweep runs **hourly at :05**. For each organization (discovered on the narrow bypass role),
  inside that organization's own transaction, it upserts the rows for **the organization's
  current local day**. The last capture of a day is the day's value.
- **From facts:** `pipeline_value` (one row per currency), `open_deals`, `weighted_pipeline`,
  `customers`, `open_tasks`.
- **Pulled from operations** (`/internal/v1/insights/snapshot`, 5 s timeout, 1 retry):
  `stock_value`, `low_stock_items`, `out_of_stock_items`. An organization whose response reports
  `item_count = 0`, and which has no earlier capture, is skipped rather than recorded as zero.
- **Failure:** a missed hour is invisible, because the next capture overwrites the same day. A
  missed day is a gap, handled by the anchor rule in §2. Three consecutive organizations failing
  against operations trips the circuit for the rest of that run.
