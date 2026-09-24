# Insights: API

Part of [ADR-0020](../adr/0020-insights-projection.md).

- **Public routes** sit under `/api/v1/insights` on the **app gateway**, with route table entry
  `{"prefix": "/api/v1/insights", "upstream": <insights>}`. There is no `product` key, as for
  `/api/v1/approvals`, because the route spans products and each widget checks its own
  entitlement.
- The routes are read-only, and errors are the platform's problem+json.
- The **internal routes each source adds** are specified in §6.

## 1. Who may call, and what "state" means

The endpoints require an authenticated **tenant** principal, forwarded by the gateway. They
require no permission of their own. Every metric, series, ranking and activity kind is then gated
separately by `app/access.py:gate(principal, key)`:

| `state` | When | What the SPA does |
|---|---|---|
| `ok` | Entitled, permitted at the minimum scope, and the value is computable | Render |
| `not_active` | The organization has not activated the product (`ent` lacks it) | Hide the widget. A holder of `core.product.activation.write` sees the audit's "{Product} isn't active" card. |
| `no_access` | The permission is missing, or below the metric's minimum scope | Do not render. The page never shows "no access" per card. |
| `insufficient_history` | The window starts before the metric's `history_from` (metrics.md §3) | Render nothing, or the chart's "appears once…" line |
| `unavailable` | A pulled capture's source is down with no capture, or the computation failed | Inline "Couldn't load {thing}." + **Try again** |

`no_access` for a widget is a 200 inside the envelope, never a 403 for the request. One card's
denial must not blank the page, and a caller learns nothing from it they do not already know
about their own grants.

**Scope captions.** Every widget returns the `scope` it was computed at (`ORG` or `OWN`), and the
SPA captions from it ("All deals" vs "Your deals"). A TEAM or LOCATION grant is computed and
reported as `OWN`, narrowing, never widening.

## 2. Common parameters

| Parameter | Values | Default | Validation |
|---|---|---|---|
| `period` | `today` \| `this_week` \| `this_month` \| `this_quarter` \| `last_30_days` \| `last_month` \| `custom` | `this_month` | 422 on anything else |
| `from`, `to` | ISO dates, in the organization's timezone, inclusive | none | Required with `custom` and refused without it. `from ≤ to ≤ today`, span ≤ 366 days. |
| `compare` | `previous_period` \| `same_period_last_year` \| `none` | `previous_period` | |

Period and comparison arithmetic is metrics.md §2. Every response echoes the resolved windows and
the timezone, so the SPA never computes a date.

## 3. `GET /api/v1/insights/summary`

KPI values and their comparisons, for up to 16 metric keys in one call.

| Parameter | Notes |
|---|---|
| `metrics` | Comma-separated keys from metrics.md §4, from 1 to 16. An unknown key is a 422, whether or not the caller could see it (keys are public vocabulary, not data). |
| `period`, `from`, `to`, `compare` | §2 |

```json
{
  "as_of": "2026-09-25T10:15:04Z",
  "timezone": "Asia/Kolkata",
  "currency": "INR",
  "period": { "key": "this_month", "from": "2026-09-01", "to": "2026-09-25", "label": "1–25 Sep 2026" },
  "comparison": { "key": "previous_period", "from": "2026-08-01", "to": "2026-08-25", "label": "1–25 Aug 2026" },
  "metrics": {
    "revenue": {
      "state": "ok",
      "kind": "flow",
      "unit": "money",
      "scope": "ORG",
      "value": 124832000,
      "currency": "INR",
      "other_currencies": [{ "currency": "USD", "value": 1200000 }],
      "comparison": { "state": "ok", "value": 111457143, "delta_pct": 12.0 },
      "history_from": null
    },
    "deals_won": {
      "state": "ok", "kind": "flow", "unit": "count", "scope": "OWN",
      "value": 4,
      "comparison": { "state": "no_baseline", "value": 0, "delta_pct": null },
      "history_from": null
    },
    "win_rate": {
      "state": "ok", "kind": "percent", "unit": "percent", "scope": "OWN",
      "value": 57.1,
      "comparison": { "state": "ok", "value": 50.0, "delta_pp": 7.1 },
      "history_from": null
    },
    "pipeline_value": {
      "state": "ok", "kind": "state", "unit": "money", "scope": "ORG",
      "value": 98100000, "currency": "INR", "other_currencies": [],
      "comparison": { "state": "insufficient_history", "value": null, "delta_pct": null },
      "history_from": "2026-10-02"
    },
    "stock_value": {
      "state": "ok", "kind": "state", "unit": "money", "scope": "ORG",
      "value": 45230000, "currency": "INR", "other_currencies": [],
      "as_of": "2026-09-25T10:05:11Z",
      "comparison": { "state": "ok", "value": 41870000, "delta_pct": 8.0, "anchor_day": "2026-08-25" },
      "history_from": "2026-08-01"
    },
    "customers": { "state": "no_access" },
    "enquiries_received": { "state": "not_active", "product_key": "storefront" }
  }
}
```

The fields, by metric kind:

- **Money metrics** carry `currency`, which is the organization's, and `other_currencies`.
  Currencies are never summed.
- **Percent metrics** carry `delta_pp`, not `delta_pct`.
- **Current metrics** (`overdue_tasks`) carry no `comparison`.
- **Pulled state metrics** carry their own `as_of`, which is the capture time.

`state` and `comparison.state` are independent: a value can be `ok` while its comparison is
`insufficient_history`.

## 4. `GET /api/v1/insights/series`

One metric over time, for a chart.

| Parameter | Notes |
|---|---|
| `metric` | One key. `current`-kind metrics (`overdue_tasks`) are refused with a 422. |
| `grain` | `day` (≤ 92 points), `week` (ISO, ≤ 104), `month` (≤ 36). A 422 beyond these. |
| `from`, `to` | Required. Local dates, snapped outward to whole buckets. |
| `breakdown` | Only `source`, only for `leads_created` in v1 |

```json
{
  "as_of": "2026-09-25T10:15:04Z",
  "timezone": "Asia/Kolkata",
  "metric": "revenue",
  "state": "ok",
  "scope": "ORG",
  "unit": "money",
  "currency": "INR",
  "grain": "month",
  "history_from": null,
  "points": [
    { "start": "2025-10-01", "end": "2025-10-31", "value": 98210000, "complete": true },
    { "start": "2026-09-01", "end": "2026-09-30", "value": 124832000, "complete": false }
  ],
  "other_currencies": [{ "currency": "USD", "points": [{ "start": "2026-09-01", "value": 1200000 }] }]
}
```

The rules for points:

- A bucket that starts before `history_from` is **omitted**, never zero-filled.
- A covered bucket with no rows is `0`, which is a real zero.
- The bucket containing today is `complete: false`.
- A flow's point is its sum.
- A state metric's point is the bucket's last capture, or for `headcount` the as-of value at the
  bucket's end.
- A percent metric's point is computed over the bucket.

With `breakdown=source`, each point carries `values: {"form": 12, "import": 3, "manual": 7}` in
place of `value`.

## 5. `GET /api/v1/insights/activity` and `GET /api/v1/insights/rankings`

### Activity

| Parameter | Notes |
|---|---|
| `limit` | 1–50, default 10 |
| `before` | An opaque cursor from `next_cursor` (keyset on `occurred_at, kind, id`) |
| `products` | Optional comma-separated product keys to restrict to, e.g. `projects` for the PM dashboard |

```json
{
  "as_of": "2026-09-25T10:15:04Z",
  "items": [
    {
      "id": "deal_won:5f0c…",
      "occurred_at": "2026-09-25T09:58:02Z",
      "product_key": "crm",
      "kind": "deal_won",
      "subject": { "type": "deal", "id": "5f0c…", "label": "Rohit Traders — annual contract" },
      "amount": { "value": 1200000, "currency": "INR" },
      "mine": true
    },
    {
      "id": "invoice_paid:9a1e…",
      "occurred_at": "2026-09-25T09:31:40Z",
      "product_key": "finance",
      "kind": "invoice_paid",
      "subject": { "type": "invoice", "id": "9a1e…", "label": "INV/2026-27/0264" },
      "amount": { "value": 1200000, "currency": "INR" },
      "mine": false
    }
  ],
  "next_cursor": "opaque-keyset-cursor"
}
```

The SPA builds the link from `product_key` + `subject.type` + `subject.id` through its existing
`PRODUCT_HOSTS` map. A kind the caller cannot see is simply absent, with no placeholder. The kinds
and their gates are metrics.md §6.

### Rankings

`board` is `sales_reps`, `task_assignees` or `enquired_products`, with `period`, `from`, `to`
from §2 and `limit` (1–10, default 5).

```json
{
  "as_of": "2026-09-25T10:15:04Z",
  "board": "sales_reps",
  "state": "ok",
  "scope": "ORG",
  "period": { "key": "this_month", "from": "2026-09-01", "to": "2026-09-25" },
  "currency": "INR",
  "rows": [
    { "person_ref": "3c7d…", "values": { "deals_won": 4, "deals_won_value": 5400000, "deals_created": 9 } },
    { "person_ref": "81aa…", "values": { "deals_won": 2, "deals_won_value": 2150000, "deals_created": 5 } }
  ]
}
```

`enquired_products` rows carry `product_ref` and `label`, not `person_ref`. The definitions are
metrics.md §5.

## 6. Internal routes the sources add (service-to-service only)

These are all mounted on each source's existing `/internal/v1` router (`include_in_schema=False`,
`require_service_token`), with a **`TRUSTED_CALLER = "insights"`** check as in
`subscription/app/api/internal.py:require_console_caller`. All of them are read-only.

### 6.1 Entity export: `GET /internal/v1/insights/export/{entity}`

| Parameter | Notes |
|---|---|
| `organization_id` | Required. Bound with `set_tenant()` as the first statement, on the NOBYPASSRLS app role, so RLS confirms every row is that organization's. |
| `after_updated_at`, `after_id` | Keyset cursor. Rows are ordered by `(updated_at, id)` ascending. |
| `limit` | 1–500, default 500 |

The response is `{"rows": [...], "next": {"after_updated_at": "…", "after_id": "…"} | null}`.
Each row has:

- the entity's **full-state event fields** (events.md §2);
- `id`, `created_at`, `updated_at`, `deleted_at`;
- soft-deleted rows included, so the deletion propagates.

| Service | `entity` | Row fields beyond the event's (for export only) | Slice |
|---|---|---|---|
| finance | `invoices` | `paid_at`: the `created_at` of the payment whose allocation settled it, `null` unless `status = 'paid'` | I2 |
| finance | `payments` | `currency`, derived from the allocated invoices as in the event | I2 |
| finance | `purchases` | none | I2 |
| deal | `deals` | none (`closed_at` is the new column) | I3 |
| lead | `leads` | `converted_at` | I4 |
| customer | `companies` | none | I4 |
| operations | `purchase_orders` | `received_date` | I5 |
| projects | `tasks` | none (`completed_at` is the new column) | I6 |
| storefront | `enquiries` | `lines` (as in the event), `status` | I7 |
| employee / attendance / leave | `employees` / `records` / `requests` | the dates and statuses the I9 facts need, and nothing else | I9 |

### 6.2 Organizations: `GET /internal/v1/organizations/export` on account (I1)

This is a cross-tenant list on account's existing **directory session**, so slice I1 adds it to
ADR-0002's enumerated bypass paths.

- **Columns**, and only these: `organization_id`, `timezone`, `currency`,
  `fiscal_year_start_month`, `status`, `updated_at`.
- It is keyset-paginated on `(updated_at, id)`, 500 per page, for trusted caller `insights`.
- No name, slug, domain or address leaves account on this route.

### 6.3 Stock capture: `GET /internal/v1/insights/snapshot?organization_id=` on operations (I5)

This route is tenant-bound like §6.1, and computed by operations' own `domain` functions:

```json
{ "organization_id": "…", "currency": "INR", "inventory_value_minor": 45230000,
  "low_stock_count": 12, "out_of_stock_count": 3, "item_count": 418, "computed_at": "2026-09-25T10:05:11Z" }
```

If items are held in more than one currency, `inventory_value_by_currency` replaces the single
pair. Operations' `unit_value_minor` carries a currency per item.

## 7. Caching and freshness

- **Gate first, then cache.** The access decision is computed on every request from the
  forwarded principal, and **never cached**. Redis holds only computed numbers.
- The key is `insights:v1:{org}:{metric|board}:{scope_key}:{window}:{compare}`, where
  `scope_key` is `ORG` or `OWN:{subject_ref}`. The TTL is 60 s.
- If Redis does not answer within 50 ms, insights computes directly. Caching fails open;
  authorization never depends on Redis.
- `/activity` is not cached. It is an index-backed `UNION ALL` of at most 12 limited subqueries.
- Responses carry `Cache-Control: private, max-age=30`.
- **Freshness:** a flow is at most 60 s plus event lag behind its source. A pulled capture's own
  `as_of` says how old it is, at most ~1 h. `as_of` on the envelope is the server's time.

## 8. Tests every endpoint ships with (negative authorization first)

1. **Each gate.** For each metric key, ranking board and activity kind: without the permission the
   result is `no_access`; without the entitlement it is `not_active`; below the minimum scope an
   ORG-only key gives `no_access`.
2. **OWN isolation.** An OWN-scoped caller's figures, rankings and feed rows equal those computed
   over **only** their `owner_ref`. Another owner's row in the same organization changes nothing
   they see.
3. **TEAM and LOCATION narrow to OWN**, with `scope: "OWN"` in the response.
4. **Organization isolation under RLS.** Facts for organization B are invisible to a request
   bound to A, even when the query's explicit filter is removed (RLS as the net).
5. **Feed exclusion.** A caller with finance but not CRM sees no `deal_*`, `lead_*` or `company_*`
   item, and `next_cursor` does not skip over them in a way that leaks their existence
   (timestamps are only of visible rows).
6. **Coverage.** A comparison window before `history_from` returns `insufficient_history`, never a
   number.
7. **Internal routes.** A token from any caller other than `insights` gets a 401 on every §6
   route. A row of another organization named in a request resolves to nothing under the bound
   tenant.
