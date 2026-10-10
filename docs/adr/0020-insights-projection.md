# ADR-0020 — Cross-product insights: a projection service behind the role dashboards

Status: **accepted (design)** · Date: 2026-09-25 · Build: slices I1–I9 below, none started

Builds on [ADR-0000](0000-phase-0-decisions.md) §7 (the envelope), §8 (money), §10 (shared
schema, RLS, transaction-local GUC, no cross-schema FKs); [ADR-0002](0002-row-level-security.md)
(the RLS paths and the named bypasses); [ADR-0003](0003-surfaces-and-authorization-topology.md)
(operators see no tenant business data); [ADR-0006](0006-approval-engine.md) §2 (events carry
full state, never a diff); [ADR-0013](0013-self-serve-products-and-the-one-box-deployment.md)
(one box, and its memory budget); [ADR-0015](0015-scheduler-and-not-before-delivery.md) §3
(discovery is cross-tenant, action is per-tenant).

Supporting documents, which are part of this decision:

- [`docs/insights/data-model.md`](../insights/data-model.md): every table, column, key, index,
  RLS policy and retention rule.
- [`docs/insights/metrics.md`](../insights/metrics.md): every metric's exact definition, its
  comparison period, its history coverage and its gate.
- [`docs/insights/api.md`](../insights/api.md): endpoints, parameters, response schemas,
  per-widget authorization, caching, and the internal export routes on each source.
- [`docs/insights/events.md`](../insights/events.md): every routing key consumed, whether it
  exists as-is, needs fields, or needs a new emission, and the producing code path.
- `libs/contracts/events/*/v1.schema.json`: the 37 event contracts this ADR commits (§6).

---

## Context

The owner approved role-aware dashboards on 2026-09-25 (UI modernization brief §4 and §5). Brief
§23 forbids fake or hardcoded metrics. The dashboards data audit of the same day mapped 110
metrics across seven roles: **53 available, 28 derivable, 29 missing.** Almost every missing row
is one of four things nothing in the platform can answer today:

1. **History.** Every "vs last month" and every time series. Product services store current
   state. Where a trend exists on the wire it is a constant, some of them unflagged
   (`/deals/summary.trend[0..6]`, `/projects-workspace/overview.burndown_*`).
2. **Cross-product views.** A recent-activity feed across CRM, finance, operations, projects and
   storefront, and a home that summarises all of them without ten scans.
3. **Team performance**, which no service defines.
4. **Orders and top products**, which have no entity behind them.

An earlier data design for the same home, drafted with the owner on 2026-09-24
(`.claude/scratch/dash/project/DataDesign.dc.html`), proposed an insights consumer with counter
tables (`daily_metrics`, `status_counts`, `activity_feed`, `top_items_daily`,
`team_utilisation_weekly`, `processed_events`) and `GET /api/v1/home/summary` + `/home/activity`.
This ADR keeps its purpose, its per-widget-access rule and its "no personal data" rule, and
changes its storage model (§2), its API surface (§7), and what it does about orders, top products
and utilisation (§10).

The constraints are the platform's own:

- one Postgres cluster, a schema per service, no cross-schema reads or foreign keys;
- every table carries `organization_id`, under RLS with a transaction-local tenant GUC;
- consumers are idempotent;
- money is integer minor units plus an ISO currency;
- projections are derived and rebuildable;
- authorization is server-side, each widget is gated by the caller's real grants, and no figure
  shows a caller data they could not read in the source product;
- the production edge is one 3,862 MB VM that already prices at 3,944 MB for the ten sellable
  profiles plus admin (`memory-budget.json`, 102%). Preflight refuses anything above 125%
  (4,827 MB), even with the owner's accepted ceiling.

---

## Decision

### 1. A new `insights` service, one container, three loops in one process

**Recommended: a new service, `insights`, owning schema `insights`.** It consumes domain events,
keeps a per-organization projection, and serves a read-only tenant API at `/api/v1/insights/*`
through the app gateway. It is a **leaf**: no service ever calls it. Only the SPAs read it, so its
failure degrades dashboards and nothing else.

| Option | Memory | Ownership | Rebuildability | Verdict |
|---|---|---|---|---|
| **A. New `insights` service**, consumer + sweeps folded into its API process | +99 MB (one `api_with_worker` container, the measured kind). Replaces the `analytics-api` + `analytics-worker` placeholders (188 MB projected), so the projection drops by 89 MB. | One owner for "over time" and "across products". Sources keep "now". | Facts: fully, from the sources' export routes. Snapshots: not rebuildable by nature (§2). | **Chosen** |
| B. Extend `console` | 0 MB | Console is the operator surface on the admin gateway, and reads cross-tenant on a BYPASSRLS directory role. Serving tenant business aggregates from it breaks ADR-0003's split in both directions. | Same as A | Rejected: wrong surface, wrong role |
| C. History inside each product service, with the home fanning out | 0 MB new containers | Each source grows its own snapshot table, sweep and series endpoint: five copies of one mechanism | Per service | Rejected: no cross-product feed or team view is possible, and a Home would still make ~10 calls |
| D. Fold the insights module into an existing always-on tenant service (account, notification) | 0 MB | Two bounded contexts in one schema and one deployable. `import app` means it cannot be a second app in the same process. | Mixed with unrelated data | Rejected: ownership |
| E. Nothing on the backend: the frontend scans | 0 MB | None | n/a | Rejected: the audit prices it at ⌈N/200⌉ calls per figure, and it still has no history for state metrics |

**Why the consumer folds into the API process.** The worker-folding follow-up (2026-09-24)
measured the cost of a folded loop at +1 to +1.4 MiB on top of the API's ~98 MB. A separate
`insights-worker` container would cost another ~90 MB of mostly duplicated module graph. Three
loops share one process, as `notification` already runs two consumers and a driver:

1. the **event consumer** (queue `insights.events`);
2. the **snapshot sweep** (hourly; also retention and the weekly reconcile trigger);
3. the **backfill runner** (drains `backfill_runs`).

All three run on the platform's two-connection bulkhead engine (`create_bulkhead_engine`), never
the request pool. `insights-worker` exists in compose behind the `split-workers` profile as the
rollback, exactly like the other ten.

**Cost on the box.** 3,944 + 99 = 4,043 MB, which is 104.7% of MemTotal and under the 125% cap.
Going live needs the owner to raise `EDGE_BUDGET_ACCEPTED_MB` by 99 (preflight prints the exact
figure). This ADR does not make the box bigger. It keeps the addition to one container, and it
retires 188 MB of planned headroom that is now spoken for.

**Profile.** Core (`profile: null`). The tenant home serves every organization, whatever it has
activated. `planned.insights-api` in `memory-budget.json` replaces the analytics placeholders in
this change. The slice that lands the container deletes it, as `test_planned_and_containers_
never_overlap` requires.

### 2. The projection's shape: entity facts plus captured snapshots, and no counters

The earlier draft stored **counters** (`daily_metrics.value += …`). Counters cannot be reconciled
with a backfill without double counting. They cannot absorb a correction (an invoice voided, a
deal re-valued) without a compensating event that nothing emits. And a lost event makes them
wrong forever with no way to tell. So insights stores **state, never increments**:

- **Entity facts.** One row per source entity: invoice, payment, purchase document, lead,
  company, deal, purchase order, task, enquiry, and in slice I9 employee, attendance day and leave
  request. Each row holds a thin, typed slice of the source row (dates, status, amounts,
  currency, the OWN-scope owner reference and a business label), keyed
  `(organization_id, id)` with the **same UUID as the source row**. Writes are upserts guarded by
  a source version:

  ```
  apply iff incoming.source_updated_at >= stored.source_updated_at
  ```

  An event's version is its envelope `occurred_at`, which is the outbox row's `created_at`, the
  source transaction's `now()`. A redelivered or reordered event therefore never overwrites a
  newer one, and a replay of the same version rewrites identical values.

  **Export rows follow a different rule**, because they are point-in-time reads. An exported row
  overwrites the fact **unless an event touched the fact after insights issued that page's
  request** (`fact.ingested_at >= page_requested_at`). An event folded before the request was
  for a change that had already committed, so the export shows that change or a newer one. An
  event folded after the request may be newer than the page, so the page yields to it, and the
  next event or re-export settles it. This makes events and backfill **commute**, which is what
  lets the backfill run while the consumer is live (§3).

  **The one known inversion.** `now()` is the transaction's *start* time. Two concurrent
  transactions on the same row can therefore commit in the opposite order to their stamps, and
  the event guard would keep the older state. It lasts until the entity's next change or the
  weekly re-export, whose overwrite rule above does not compare versions at all. A monotonic
  per-row version column in every source would remove it. That is eight schema changes to fix an
  anomaly the weekly reconcile already bounds, so it is recorded here, not built.

- **Flows are computed at query time from facts.** Examples are "revenue this month", "deals won
  last week", "tasks completed per week" and "on-time delivery in August". Each is a `GROUP BY`
  over an org-leading index. The largest realistic tenant (§11) has under 100k rows per fact
  table. No rollup table is added until the first trigger under "Revisit when" fires. A rollup is
  a second copy that can drift, and it has to earn its place.

- **Snapshots** hold state whose history no source keeps: open pipeline value, open deals,
  weighted pipeline, customer count, open tasks, stock value, low and out-of-stock counts. They
  are captured **hourly** into `daily_snapshots(organization_id, metric, dimension, day)`, and
  the day's last capture is the day's value. They are computed from local facts, except stock
  valuation, which is **pulled** from operations (§5). Snapshots are ORG-grain only.

- **Recent activity is not a table.** It is a query-time `UNION ALL` over the fact tables' own
  timestamps (`created`, `closed`, `completed`, `paid`), filtered per kind by the caller's gates
  (§4). No second copy is kept, no feed retention job is needed, backfill fills it automatically,
  and it cannot disagree with the facts.

**Rebuildability, stated honestly.** Facts, coverage and the activity feed are fully rebuildable:
re-run the backfill (§3), and a mark-and-sweep removes facts whose source row is gone. **Snapshots
are recorded observations, not derivations.** No source keeps the history they record, so nothing
can rebuild them after the fact. The earlier draft's `status_counts` had the same property without
saying so. `daily_snapshots` is therefore treated like `console.revenue_events`:

- a closed day's rows are never rewritten;
- a rebuild never truncates them;
- they sit in the same Postgres the nightly `backup.sh` already covers.

Reconstructing state history from each service's `audit_logs` was considered and **rejected**.
Audit rows are an accountability record, not a complete event store. Seed, import and
`created_at`-override paths write state without matching audit rows, so a reconstruction would be
silently wrong for some tenants, which is the one failure §23 forbids. The narrow exception is §3's
two column backfills, where each owning service reads **its own** audit rows for one timestamp and
falls back to "unknown".

### 3. History that predates the service: backfill through internal export routes

Insights never reads another schema. Each source service exposes read-only, keyset-paginated
export routes:

```
GET /internal/v1/insights/export/{entity}?organization_id=&after_updated_at=&after_id=&limit=500
```

These follow the pattern `subscription/app/api/internal.py` already uses:

- `require_service_token`, plus a narrower check that the caller is **`insights`**
  (`TRUSTED_CALLER`);
- `set_tenant(organization_id)` as the first statement, on the NOBYPASSRLS app role, so RLS
  confirms every row belongs to the named organization;
- rows ordered by `(updated_at, id)`, soft-deleted rows included, so a deletion propagates;
- each row shaped exactly like the matching event's full-state payload.

The full list and shapes are in `docs/insights/api.md` §6.

**Organizations come from account.** Account gains
`GET /internal/v1/organizations/export`, which returns only `organization_id`, `timezone`,
`currency`, `fiscal_year_start_month`, `status` and `updated_at`, for trusted caller `insights`,
on account's existing directory session. That is a **new cross-tenant read path**. Slice I1 adds
it to ADR-0002's enumerated list, exactly as account's internal module docstring requires.

**Two source columns are added, because no timestamp exists for them today:**

- `deal.deals.closed_at`, set by `move_stage` (and by `create_deal` for a deal created closed);
- `projects.tasks.completed_at`, set when a card enters `done` and cleared when it leaves.

Each owning service backfills its column in its own migration from **its own** `audit_logs`: the
latest `deal.stage_changed` or task-update row whose `after` stage equals the current stage, else
the `created` row carrying that stage. Where no audit row exists, the column stays `NULL`, and
insights' coverage for that organization's won/lost/completed metrics starts at go-live (see
coverage, below). An unknown date is never guessed.

**The run, in order, which makes it safe to run once on production:**

1. The deploy applies migrations. Insights' are a new schema only. Sources' are additive (the two
   nullable columns plus their own-schema data backfill).
2. The insights process starts. The consumer declares `insights.events` and binds every routing
   key **before** anything else runs. The backfill runner waits on that signal.
3. The runner enumerates organizations from account and enqueues one `backfill_runs` row per
   (organization, entity) where no completed run younger than seven days exists. After a fresh
   deploy that is every pair.
4. The runner drains one run at a time, with one request in flight across all sources:
   - pages of 500, with a 100 ms pause between pages;
   - each page upserts facts and advances the run's cursor **in the same transaction**, so a
     crash resumes from the last page;
   - timeouts are 5 s to connect and 30 s to read, with 3 retries at 2/8/30 s. After that the run
     is marked `failed` and retried next hour, up to 5 attempts;
   - a run for a source whose container is not running (profile off) fails fast and is retried
     the same way.
5. When a run completes, facts of that (organization, entity) not seen in it, and not written
   since it started (`ingested_at < started_at`), are marked deleted (mark-and-sweep). Coverage
   is then recomputed for the metrics that depend on that entity.

Binding before exporting is what closes the gap. A change committed before the bind is in the
export, because the export reads after the bind. A change committed after the bind arrives as an
event. §2's two write rules make the overlap harmless.

**Self-healing.** The same runner re-exports every (organization, entity) weekly (Sunday 02:00
IST). A lost event, a dead-lettered event or a hard delete therefore costs at most a week of
staleness, not a permanent error. `bin/backfill --org <id> [--entity finance.invoices]` enqueues a
manual run. `bin/backfill --verify` prints per-organization fact counts and this month's sums next
to the sources' own figures, and is run after the release (slice I8).

**Coverage.** `metric_coverage(organization_id, metric, history_from, basis)` records, per
organization, the first day each metric is complete from:

- **complete** for flows over dated source rows (invoices, payments, purchases, leads, deals
  created, POs, enquiries);
- **the source column's** coverage for won, lost and completed, which is go-live where §3's
  migration left any `NULL`;
- **the first capture** for snapshot metrics.

The API never returns a comparison or a series point earlier than `history_from`. It returns
`insufficient_history` instead, so a delta is either real or absent, which is the audit's
§5.0 rule 10.

### 4. Authorization: the source's own gate, per widget, at one chokepoint

Every metric, series, ranking and activity kind is gated by **the same entitlement and permission
that guard the source route for the counted thing**. This is the `itsm/overview` pattern: one
read gate per counted thing. The gateway already forwards every grant the membership holds
(`X-Perms`) and the entitlements (`X-Entitlements`). The insights route itself carries no product,
like `/api/v1/approvals`, so the gate is evaluated **inside the service, per widget**:

- entitlement missing gives state `not_active` (the product is not active for the organization);
- permission missing, or below the metric's minimum scope, gives `no_access`;
- otherwise the widget is computed at the caller's scope.

**The chokepoint is `app/access.py:gate(principal, key) -> Gate`**, and every read in
`app/selectors.py` takes a `Gate` and starts from `scoped(stmt, gate, owner_col)`. That function
filters `organization_id` explicitly (RLS is the net, not the filter) and then, for OWN, applies
the platform's own `ScopeContext.apply(owner_col=…)`. Three rules follow:

- A fact's `owner_ref` is **by contract the value the source's OWN scope compares with
  `subject_ref`**: `deals.owner_id`, `leads.owner_id`, `companies.owner_id`,
  `purchase_orders.raised_by_id`, `tasks.assignee_id`. An OWN-scoped caller sees exactly the rows
  the source would show them.
- **TEAM and LOCATION narrow to OWN**, never widen. Insights has no team or location columns, and
  `ScopeContext.apply` refuses those rungs without one. The response says `scope: "OWN"`, so the
  caption is honest ("Your deals").
- **Finance metrics and every state metric require ORG.** Every finance permission is ORG/ORG
  already. Pipeline value, customers, open tasks and stock value are org-wide by construction,
  and snapshots have no owner dimension, so an OWN-scoped caller gets `no_access` and the
  product's own summary serves their own slice. Flows over owned rows (leads, deals, POs, tasks)
  are OWN-capable.

A caller with no grants at all gets `no_access` for every widget. That reveals nothing: the
caller already knows their own grants. The endpoint itself needs only an authenticated tenant
principal. Operator tokens never reach it, because the admin gateway has no route to insights.

**No personal data.** Insights stores no email, phone, address, government identifier, person's
name or free text. People appear only as the source's opaque id (`owner_ref`, `assignee_ref`,
`employee_ref`). Rankings return those ids, and the SPA resolves names through the product's own
directory reads (`/customers/owners`, `/projects-workspace/members`), which carry their own gates.
Business labels are kept because the source shows them to the same gate:

- invoice, PO and enquiry numbers, deal names, company names;
- task titles, catalogue product names, supplier names.

Fields that events carry and insights must drop are listed in `events.md` §4, for example leave
and cancellation `reason` text, and employees' `display_name`.

Every endpoint ships with negative-authorization tests: one per gate, OWN isolation, the
entitlement gate, TEAM narrowing, and cross-organization isolation under RLS. The list is in
`api.md` §8.

### 5. Snapshots of stock are pulled, not rebuilt from stock events

Stock value is `Σ on_hand_milli × unit_value_minor`, rounded once per item (ADR-0014 §2,
`operations/app/domain.py:inventory_value_minor`). Rebuilding it in insights would mean consuming
`operations.inventory.stock.posted`, the platform's highest-volume event, and re-implementing a
rounding rule that operations owns. Instead, the hourly sweep calls operations'
`GET /internal/v1/insights/snapshot?organization_id=`. It gets back `inventory_value_minor`, the
`currency`, the low and out-of-stock counts and the item count, computed by operations' own
functions, and stores them as that day's capture. That is one definition, owned by the service
that owns the stock. `stock.posted`, `stock.reserved` and `stock.released` are **not** consumed,
and get no schema in this change (`events.md` §3).

### 6. Event contracts

Insights binds **28 routing keys** in slices I1–I7, and slice I9 adds 8 HR keys. Today only
`crm.lead.*` and `engage.*` have committed schemas, so this change commits **37 new schemas** under
`libs/contracts/events/`: the 36 bound keys, plus `hrms.time.attendance.checked_out`. Insights does
not bind that one. It is committed so the attendance record's lifecycle has one complete contract
rather than two thirds of one. The contract tooling (`make contracts-check`) allows a schema without a
verified producer. Each schema's `$comment` states whether the producer is ahead of it:

| Status | Count | Meaning |
|---|---|---|
| **Exists as-is** | 10 | Emitted today, exactly as the schema says: `crm.pipeline.deal.deleted`, `crm.book.company.deleted`, `operations.procurement.purchase_order.{closed,cancelled,deleted}`, `projects.board.task.deleted`, `hrms.time.attendance.{checked_in,checked_out}`, `hrms.time.leave.{requested,cancelled}` |
| **Exists, needs fields** | 25 | Emitted today, but missing the full-state fields a projection needs (ADR-0006 §2). The additions are **additive** within v1. Approval and leave read named keys only. Notification's rule engine lists 16 of the 37 keys in `SUBSCRIBABLE_EVENTS`, and 14 of those gain fields. They become new `{{payload.…}}` template variables, but only for the same tenant's own rules. The `$comment` names the missing fields and the producing function. |
| **New emission** | 2 | `finance.billing.purchase.recorded` (`finance/app/services.py:record_purchase_document`) and `storefront.enquiry.status_changed` (`storefront/app/services.py:update_enquiry_status`). |

The per-event table, with producer path, fields and slice, is `events.md` §2. Each producing slice
adds a test that builds the event through the real write path and calls
`platform_core.events.validate_event_schema` on it, as `services/notification/tests/
test_event_contracts.py` already does. That test is what turns "ahead of the producer" into
"conforms".

Finance's payments table has **no currency column**, and `amount_minor` is stored bare. That
contradicts ADR-0000 §8. Slice I2 derives the currency from the allocated invoices, and refuses a
payment that allocates across currencies. Whether to add the column is finance's call.

**Amended 2026-10-07 — the stage vocabulary moves from keys to `outcome`.** The
`crm.pipeline.deal.created`, `.updated` and `.stage_changed` schemas carry six literal stage keys,
and §9's metric definitions test `closed_won` and a "closed stage" by key (the "Deals won" and
"Pipeline value" definitions). [ADR-0038](0038-crm-programme-decisions.md) proposes
(pending D-5, D-12) that stages become configurable, with an immutable key and an `outcome` of
`open`, `won` or `lost`, that consumers test the outcome and never the key, and that this ADR's
projection is rebased onto `outcome` when the stages land (R2.3); the deal migration that adds
`closed_at` and exists on the unmerged branch `sprint/insights-q2`, not on `main`, is adopted
unchanged. Until the owner answers, the six keys stay the contract.

### 7. API

All routes are under `/api/v1/insights` on the app gateway, read-only, with problem+json errors
like every other service:

| Route | Purpose |
|---|---|
| `GET /summary?metrics=…&period=…&from=&to=&compare=…` | KPI values plus comparison, for up to 16 metric keys in one call. Each metric carries its own `state`. |
| `GET /series?metric=…&grain=day\|week\|month&from=&to=&breakdown=` | One metric over time (≤ 92 days, 104 weeks or 36 months of points). Points before `history_from` are omitted, never zero-filled. |
| `GET /activity?limit=&before=&products=` | Cross-product recent activity, 12 kinds, each gated separately. Keyset-paginated. |
| `GET /rankings?board=sales_reps\|task_assignees\|enquired_products&period=…` | Team performance and most-enquired products |

**Date ranges are first-class**, which closes the audit's "no summary endpoint accepts a range".
The periods are:

- `today`, `this_week`, `this_month` (the default), `this_quarter` (fiscal, from the org's
  `fiscal_year_start_month`, April by default);
- `last_30_days`, `last_month`;
- `custom` with `from`/`to` (at most 366 days).

`compare=previous_period` is the default, and `same_period_last_year` or `none` are the
alternatives. For a to-date period the previous period is **the same elapsed span**: 1–25
September compares with 1–25 August, and 1–31 March compares with 1–28 February. The exact rules
are in `metrics.md` §2.

**Caching.** Redis holds computed **numbers**, never access decisions. The gate runs on every
request, and then a per-metric cache is consulted, keyed on:

- organization, metric, window and compare;
- scope key: `ORG`, or `OWN:<subject_ref>`.

The TTL is 60 s, and a Redis failure falls through to computing. Responses carry
`Cache-Control: private, max-age=30`. Freshness is therefore ≤ 60 s plus event lag for flows, and
≤ 1 h for pulled stock value, which is reported as `as_of`.

**A card never mixes sources.** A KPI card takes its value and its comparison from the same
response. A dashboard that shows a source's live value (for example `/operations/overview`
stock value) shows it without a delta. A card that wants a delta takes both from insights.

### 8. Time: a daily grain in the organization's timezone

- **The atom is the organization-local calendar day.** The timezone comes from account's
  `organizations.timezone` via `core.org.*` and the org export. It defaults to **Asia/Kolkata**
  until known. The response always names the zone it used.
- **Business dates are used as they are.** `issued_date`, `received_date`, `document_date`, PO
  `created_date`/`expected_date`/`received_date`, `due_date`, `work_date` and leave dates are
  dates a person chose. They carry no timezone and are never converted.
- **Timestamps are bucketed at query time** (`(ts AT TIME ZONE org_tz)::date`). This covers
  `created_at`, `closed_at`, `completed_at` and `converted_at`. A timezone change re-buckets
  timestamp history, which is correct: the organization now means a different day. Snapshot days
  keep the day they were captured under.
- **Weeks are ISO (Monday start) and months are calendar months.** Quarters follow the fiscal
  year. All are derived at query time, and no weekly or monthly table exists.
- **"Today" is computed in the organization's timezone on every request.** Finance computes "this
  month" in UTC today (`finance/app/selectors.py:_today`, whose docstring predates account's
  `organizations.timezone`), a 5.5-hour disagreement at an IST month boundary. The "a card never
  mixes sources" rule (§7) already stops that showing as a contradiction on one card: the Revenue
  card takes value and delta from insights. Finance learning the organization's timezone (by
  consuming `core.org.*`, whose schemas this change commits) is a separate finance follow-up, not
  an insights dependency.

### 9. Metric definitions

The full catalogue is in `metrics.md`: 30 keys (27 in I2–I7, 3 in I9), with their gates,
comparisons and coverage. The ones the home leans on:

- **Revenue**: Σ `total_minor` of invoices and credit notes whose status is not `draft` or
  `void`, with `issued_date` in the window, in the organization's currency (other currencies are
  listed separately, never summed). This is finance's own `domain.net_revenue_for_month`, so it
  **includes GST**; see open question Q1.
- **Invoices issued**: the count of `document_type = invoice` with the same status and date
  rule. This is the honest stand-in for "Orders".
- **Deals won** / **Deals won value**: `stage = closed_won` with `closed_at` in the window.
- **Pipeline value**: Σ `value_minor` of non-deleted deals not in a closed stage, in the org's
  currency. The comparison is the snapshot at the comparison window's end.
- **Stock value**: operations' `inventory_value_minor` at the latest hourly capture. The
  comparison is the capture at the comparison window's end.
- **On-time delivery**: of POs with `received_date` in the window, the percentage where
  `received_date ≤ expected_date`. The delta is in percentage points.
- **Tasks completed**: cards with `completed_at` in the window and currently `done`.

### 10. What the 29 missing rows become

| Role | # | Missing metric | Disposition |
|---|---|---|---|
| Super Admin | 5 | Total users | **Elsewhere**: account staff users read (audit §4 rank 3). Insights never serves the operator surface (ADR-0003). |
| Super Admin | 7 | "vs last month" | **Elsewhere**: console `/admin/metrics/series` from `console.revenue_events` (rank 2). **This is where MRR history lives.** |
| Super Admin | 8 | Account growth | **Elsewhere**: the same console series |
| Super Admin | 11 | System health | **Non-goal**: link external monitoring |
| Super Admin | 14 | Pending approvals | **Dropped**: nothing needs approving |
| Super Admin | 17 | Domain column | **Elsewhere**: account/console (rank 9). Show slug meanwhile. |
| Super Admin | 20 | Users per org | **Elsewhere**: account (rank 3) |
| Super Admin | 23 | Created on list | **Elsewhere**: console list fields (rank 1) |
| Owner | 3 | Orders | **Insights, relabelled**: "Invoices issued" (finance), "Deals won" (CRM), "Enquiries" (storefront). Never labelled "Orders". |
| Owner | 9 | Orders by status | **Relabelled at the source**: "Invoices by status" (finance, available) or "Enquiries by status" (storefront, available) |
| Owner | 11 | Team performance | **Insights**: `rankings` board `sales_reps` (won count and value, created, per owner) and `task_assignees` (completed, points, open, overdue, per assignee) |
| Owner | 13 | Recent orders | **Relabelled at the source**: "Recent invoices" (finance, available) |
| Owner | 14 | Top products | **Insights, relabelled**: "Most-enquired products" (`rankings` board `enquired_products`). Top-*selling* products stays missing until invoice lines carry an item reference (non-goal). |
| Owner | 15 | Recent activity | **Insights**: `/activity` |
| Sales | 5 | Orders | **Insights**: "Deals won" |
| Sales | 12 | Pipeline value over time | **Insights**: `pipeline_value` series from snapshots. History starts at go-live. |
| PM | 3 | On hold | **Non-goal**: no such phase; a product decision |
| PM | 11 | Recent activity | **Insights**: `/activity?products=projects` |
| PM | 14 | Utilization | **Non-goal**: no capacity source. Remove the constant. |
| PM | 15 | Burndown | **Insights, replaced**: "Tasks completed per week" plus the "Open tasks" series |
| Inventory | 3 | Stock value vs last month | **Insights**: pulled hourly capture. History starts at go-live. |
| Inventory | 13 | On-time delivery | **Insights**: from PO facts, full history via backfill. Stop rendering the seeded `Supplier.on_time_pct`. |
| Inventory | 15 | Org-wide stock movements | **Elsewhere**: an operations list over its own ledger (S). Insights does not copy the stock ledger. |
| Finance | 6 | Payables | **Non-goal**: purchases have no due date or paid state; a product decision |
| Finance | 11 | Cash flow | **Insights, partial**: "Money in by month" (`payments_received` series). Money out has no source, so cash flow stays missing. |
| Finance | 12 | Cash balance | **Non-goal**: needs a bank feed or GL |
| Employee | 6 | Calendar | **Non-goal** |
| Employee | 7 | Notifications | **Non-goal**: a per-user inbox with read state belongs to notification. The activity feed is not an inbox. |
| Employee | 11 | Company policies | **Non-goal** |

**Tally:**

- **11 closed by insights**, one of them partial (Owner 3, 11, 14, 15; Sales 5, 12; PM 11, 15;
  Inventory 3, 13; Finance 11).
- **9 closed elsewhere**, by console, account, operations or a source relabel.
- **9 non-goals or drops.**

Insights also turns these derivable rows into single-call available ones:

- Owner 2, 5, 6 and 8 (revenue delta, customers delta, expenses, revenue over time);
- Sales 2 and 9 (new leads vs last month, won this month);
- PM 6 and 9 (overdue tasks, team workload);
- Finance 2, 7 and 8.

### 11. Capacity and failure model

**Expected load, year one:**

| Quantity | Figure |
|---|---|
| Tenants | 50–300 |
| Users per tenant | 5–50 |
| Largest realistic tenant | 200 users. Per year: 60k invoices, 20k deals, 100k tasks, 50k enquiries, 10k POs, 20k leads. |
| Peak API rate | Dashboard loads ≈ 300 × 20 × 10/day → ~5 loads/s at peak. Each load is ~4 insights requests, so **~20 req/s**, mostly cache hits. |
| Event rate | ~1–5/s sustained, bursts of ~50/s during imports. One fold is one short transaction (~3 ms), so a single consumer does 200+/s. |
| Data growth | Typical tenant ~2k fact rows/month (~1 MB with indexes). Largest ~25k/month (~12 MB). Snapshots ~10 rows/day/org. |
| Total at 300 tenants | ≲ 1 GB/year |

**What saturates first.** The insights process's single event loop on a shared 2-vCPU box. An
uncached summary of 8 metrics is ~8 index range scans, estimated at 20–50 ms. The process tops out
somewhere near 40–60 uncached summaries/s, well above the peak with a 60 s cache. The box's RAM
and Postgres's connection budget saturate long before insights' CPU does:

- insights adds a request pool plus 2 bulkhead connections;
- slice I1 checks `max_connections` headroom against `compose.prod.yml`'s postgres settings.

**Dependencies, and what insights does when each is slow, failing or wrong:**

| Dependency | Slow | Failing | Wrong |
|---|---|---|---|
| **Postgres** | Request timeouts (platform default). The consumer's prefetch of 16 back-pressures. | API answers 503. The consumer stops acking, and messages wait in the queue. | n/a (the source of truth for insights' own data) |
| **RabbitMQ** | Figures lag. `as_of` stays honest. | Events wait in producers' outboxes, because the relay retries. Dashboards show the last folded state. The weekly reconcile repairs anything lost. ops-monitor alerts on `insights.events` depth and on the DLQ. | A malformed or out-of-contract payload raises, and the message **dead-letters** to `insights.events.dlq` (visible, replayable with `tools/dlq.py`). The entity is repaired by the next event or the weekly re-export. Never silently dropped. |
| **Source export routes** (backfill) | 30 s read timeout, pages paced at 100 ms. | 3 retries (2/8/30 s), then the run is marked `failed` and retried hourly (max 5). Other sources and organizations continue. | An export page yields to any fresher event (§2), and the next weekly re-export overwrites again. `--verify` reports count and sum mismatches per organization. |
| **Operations snapshot route** (hourly) | 5 s timeout. | 1 retry, then that hour is skipped. If it fails for 3 organizations in a row, the rest of that run skips operations (circuit). A day with no capture is a gap: the series shows a gap, and the comparison uses the nearest capture within 3 days before the anchor, else `insufficient_history`. | Same as above |
| **Account** (organization list, timezone) | Enumeration waits. | The backfill runner retries hourly. Folds use the stored `org_profile`, or Asia/Kolkata with `timezone` named in every response. | The next `core.org.updated` corrects it. |
| **Redis** (cache) | Cache bypassed after 50 ms | Compute directly (fail open **for caching only**; the gate never touches Redis) | n/a |
| **Insights itself** | Widgets show their skeleton, then an inline error (audit §5.0 rule 7). | The same. Current-state figures from sources still render. **Nothing in the platform depends on insights.** | Parity tests against the sources' own figures (`metrics.md` §7) |

### 12. Build plan

The sizes assume one engineer: **S** ≤ 1 day, **M** 2–3 days, **L** 4–6 days. Every slice merges
behind the single release, is independently testable, and leaves `make check` green.

| Slice | Contents | Size | Unlocks (dashboard widgets) |
|---|---|---|---|
| **I0** | This ADR, the supporting docs, the 37 schemas, and the memory-budget placeholder | done | Contract review |
| **I1** Skeleton, tenancy, API shell | Contents below | M–L | The API contract is live for frontend integration: every widget renders its state. No figures yet. |
| **I2** Finance | Contents below | M | **Owner**: Revenue this month + delta, Revenue by month, "Invoices issued" (Orders), Expenses. **Finance**: Revenue delta, Received this month + delta, Money in by month, Purchases by month. **Activity**: finance kinds. |
| **I3** Deal | Contents below | M | **Sales**: Won this month + delta, Win rate (period), Pipeline value over time. **Owner/Sales**: Team performance (sales), Deals won. **Activity**: deal kinds. |
| **I4** Lead + customer | Contents below | M | **Sales**: New leads vs last month, Leads by source. **Owner**: Customers + delta (the delta appears one month after go-live). **Activity**: lead/company kinds. |
| **I5** Operations | Contents below | M | **Inventory**: Stock value + delta (after a month), On-time delivery (full history), POs raised trend. **Activity**: PO kinds. |
| **I6** Projects | Contents below | M | **PM**: Tasks completed per week (burndown replacement), Open tasks trend, Overdue tasks, Team workload / performance (delivery). **Activity**: task kinds. |
| **I7** Storefront | Contents below | S | **Owner**: Most-enquired products (Top products), Enquiries (Orders for storefront orgs). **Activity**: enquiry kind. |
| **I8** Release hardening | Contents below | S–M | Release confidence |
| **I9** HR (optional, after launch) | Contents below | M | HRMS dashboard headcount trend (today it is a sample), attendance and leave trends |

What each slice contains:

- **I1: skeleton, tenancy, API shell.**
  - `make new-service NAME=insights`, then the models, the RLS migration and the roles (dev
    `init-db.sql`, edge `postgres-init`, the remote role reconcile).
  - `app/access.py` gate catalogue, and `summary`/`series`/`activity`/`rankings` returning
    per-widget states with zero-row data.
  - Gateway route in `routes.json` and `routes.prod.json`. Compose: `insights`
    (api_with_worker) and `insights-worker` (split-workers). Budget entry, with the placeholder
    deleted. ops-monitor DLQ list. `EDGE_RELAY_SCHEMAS` parity.
  - Backfill runner, snapshot sweep framework, retention, weekly reconcile, `bin/backfill`
    (enqueue and `--verify`).
  - **Account**: `core.org.*` enrichment, the org export route, and the ADR-0002 list update.
  - Negative-authz and RLS tests.
- **I2: finance.**
  - Enrich `invoice.issued`/`invoice.paid`/`payment.received`, emit `purchase.recorded`, derive
    the payment currency (refuse mixed).
  - Export routes: invoices (with `paid_at`), payments, purchases.
  - Folds, metrics `revenue`, `invoices_issued`, `payments_received`, `purchases_recorded`, and
    activity kinds.
  - Parity test: insights' revenue equals finance's `net_revenue_for_month` on shared fixtures.
- **I3: deal.**
  - `closed_at` column plus the own-audit backfill migration. Enrich deal events. Export route.
  - Metrics `deals_created`, `deals_won`, `deals_won_value`, `deals_lost`, `win_rate`,
    `pipeline_value`, `open_deals`, `weighted_pipeline` (plus snapshots). Ranking `sales_reps`.
    Activity kinds.
  - Parity test against `/deals/summary`.
- **I4: lead + customer.**
  - Enrich lead and company events. Export routes.
  - Metrics `leads_created` (breakdown `source`), `leads_converted`, `customers` (plus
    snapshot). Activity kinds.
- **I5: operations.**
  - Enrich PO events. Export route. `/internal/v1/insights/snapshot`.
  - Metrics `purchase_orders_raised`, `purchase_orders_raised_value`, `on_time_delivery`,
    `stock_value`, `low_stock_items`, `out_of_stock_items`. Activity kind.
- **I6: projects.**
  - `completed_at` column plus the own-audit backfill. Enrich task events. Export route.
  - Metrics `tasks_created`, `tasks_completed`, `task_points_completed`, `open_tasks` (plus
    snapshot), `overdue_tasks`. Ranking `task_assignees`. Activity kind.
- **I7: storefront.**
  - Enrich `enquiry.created` with lines. Emit `enquiry.status_changed`. Export route.
  - Metric `enquiries_received`. Ranking `enquired_products`. Activity kind.
- **I8: release hardening.**
  - A production dry run: migrations plus a full backfill against a restored production copy in
    an isolated database (the `opsgrids_inv` pattern).
  - A `--verify` report reviewed by the owner.
  - `summary` p95 measured.
  - A RUNBOOK section (release order, the `EDGE_BUDGET_ACCEPTED_MB` bump, the DLQ, rollback).
- **I9: HR (optional, after launch).**
  - Enrich employee events (joining and exit dates), `attendance.regularized` (`work_date`) and
    `leave.applied` (status and dates). Export routes.
  - Metrics `headcount`, `checked_in`, `on_leave`.

**Parallel slices that close rows outside insights (§10), not blocking it:**

- console series and list fields (S + S);
- account staff users read (M);
- operations org-wide movements list (S);
- finance: delete the sample `cash`, `cashflow` and trend fields once no SPA reads them, and
  compute "this month" in the organization's timezone (S–M).

**Order relative to the frontend phases** (audit §6). Frontend step 0 (the honesty sweep) and the
seven v1 dashboards need **no** insights at all, and proceed first. Backend slices run in parallel,
in the order **I1 → I2 → I3 → I4 → I6 → I5 → I7 → I8**. That order is by value: finance gives the
Owner Home its revenue trend, CRM gives Sales, projects gives PM.

Each dashboard's "v2 pass" wires its reserved delta, series, feed and ranking slots once its slices
have merged:

- Owner Home after I2, I3, I4 and I7;
- Sales after I3 and I4;
- PM after I6;
- Inventory after I5;
- Finance after I2.

The frontend can build against `api.md`'s examples before then, because I1 serves real states
with zero data.

**Release "all at once", once, on production.** The deploy order is:

1. source services (additive migrations and enriched events);
2. insights;
3. frontends.

This is safe to run exactly once, and safe to run again:

- insights' migrations create only its own schema;
- sources' migrations add two nullable columns and backfill them from their own rows;
- no routing key is renamed, and payload changes are additive;
- the backfill is read-only against sources, paced, resumable and idempotent, and starts itself.
  Nothing is typed by hand.

**Rollback** is stopping the `insights` container. Dashboards fall back to their v1, no-delta
states, and the additive source changes are harmless to leave in place.

---

## Consequences

- One more always-on container (+99 MB) and one more queue. The owner raises
  `EDGE_BUDGET_ACCEPTED_MB` by 99 at release.
- Eight producing services change their event payloads (additively) and gain one internal export
  route each. Two gain a column. Every producer gets a payload-vs-schema test, and the platform
  gains 37 committed event contracts it did not have. Approval, leave and attendance already
  consume several of the HR ones without a contract.
- "Now" stays with the sources and "over time / across products" moves to insights. A new time
  series anywhere else is a design review question, not a local choice.
- Pipeline, customers, open-task and stock **history starts at go-live**. Their deltas appear one
  month later and are absent, not invented, until then. Flow history (revenue, leads, deals
  created, POs, enquiries, on-time) is complete from day one.
- Snapshots are the one table that cannot be rebuilt, and they must stay in the backup.

## Risks

1. **Box memory.** +99 MB on a box already at 102% of MemTotal. Mitigation: one container, folded
   loops, 89 MB of projected headroom returned. The real fix, resizing the VM, is the owner's
   decision (ADR-0013).

   **Amended 2026-10-07 — the +99 MB is an owner decision against today's totals.** Computed with
   `infrastructure/edge/lib/memory_budget_totals.py`, the ten sellable profiles total 3747 MB and the
   ten plus `admin` total 3944 MB against the 3862 MB reference, and the box's accepted ceiling is
   the owner's setting (`EDGE_BUDGET_ACCEPTED_MB`), so the +99 MB above is not covered by it and the
   CRM programme's own consumer folds (+1 MB each, the first at R2.4) draw on the same ceiling.
   Whether the ceiling rises, memory is freed or the folds wait is
   [ADR-0038](0038-crm-programme-decisions.md) D-13 (pending D-13); the programme design's row D-13
   (`docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md:388`) says the insights
   container's +99 MB ships only after a separate owner answer on the ceiling.
2. **Two definitions of one number.** Insights' revenue against finance's, and insights' pipeline
   against `/deals/summary`. Mitigations:
   - parity tests on shared fixtures in I2 and I3;
   - the "a card never mixes sources" rule;
   - finance's timezone follow-up.
3. **A producer that stops emitting**, or emits off-contract. Mitigations:
   - payload-vs-schema tests per producer;
   - the DLQ plus the ops-monitor alert;
   - the weekly re-export bounds the damage to a week;
   - `--verify`.
4. **Snapshot loss.** A lost `insights` schema loses pipeline and stock history for good.
   Mitigation: the same Postgres backup, and I8 verifies the restore covers the schema.
5. **An OWN-column drift.** If a source changes what its OWN scope compares (projects' member vs
   `sref` link is already weak, audit §2.6), insights must change with it. Mitigation: `owner_ref`
   is named in the export and event contract as "the OWN-scope column", and the source's
   negative-authz test gains an insights twin.
6. **A new cross-tenant read on account.** Mitigation: column-narrow, trusted-caller-only, listed
   in ADR-0002.
7. **Backdated source rows move closed periods.** An invoice issued today with last month's
   `issued_date` changes last month's figure. **This is correct**, because it matches the source,
   but a delta can shift between visits. The response carries `as_of`.
8. **`processed_events` has no `organization_id`.** It is the platform mixin every consumer uses.
   Insights inherits the exception rather than inventing a variant. The version-guarded upserts do
   not depend on it for correctness.
9. **Load on sources during the initial backfill.** Mitigation: one request in flight, 100 ms
   pacing, pages of 500. I8 measures it on the restored copy.

## Non-goals

- **Any operator or Super Admin metric**, including MRR history (console owns these), and any
  read of tenant data for operators.
- **Orders, order lines, top-*selling* products.** These need an item reference on invoice lines
  or a cart. That is a product decision, recorded as future work.
- **Utilisation or capacity**, burndown, "on hold", payables, cash flow and cash balance, a
  calendar, a notifications inbox, policies.
- **Support, ITSM, marketing, pulseboard and engage metrics in v1.** Their overviews are real
  today, and they join later by the same pattern.
- **Stock-ledger copies**, org-wide movements, and consuming `stock.*` events.
- **Report builders, ad-hoc queries, exports, and real-time push.**
- **Currency conversion.** Other currencies are listed, never summed.
- **Person names, contact details or free text** in the insights schema.

## Open questions for the owner

- **Q1. Revenue includes GST.** It is finance's own definition today, and insights inherits it so
  the two never disagree. The facts carry `subtotal_minor`, so switching both to net-of-tax
  revenue is a query change plus a finance change, not a backfill. **Default: keep, and caption it
  "Invoiced, incl. tax, net of credit notes".**
- **Q2. What is a working day** for attendance rates (I9)? There is no holiday calendar. **Default:
  I9 ships counts ("checked in"), not rates.**
- **Q3. The weekly reconcile window.** Sunday 02:00 IST for every organization. **Default: keep
  until an organization outside IST complains.**

## Revisit when

- `summary` p95 exceeds 300 ms uncached, or any fact table passes 5M rows for one organization.
  Then add daily rollup tables for the hot flows.
- Tenants exceed ~1,000. The hourly operations pull then becomes 1,000 calls an hour: move it to a
  batch export, or to valuation from `stock.*` events.
- The box is resized or becomes several. Then `split-workers` gives insights its own worker.
- A second reader wants the same history (reports, exports). Then decide whether snapshots become
  a platform table.
- A source gains real stage or status history (for example a deal stage-history table). Then its
  snapshot metric becomes fact-derived and rebuildable.
