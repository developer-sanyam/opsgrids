# Insights: data model

Part of [ADR-0020](../adr/0020-insights-projection.md). Schema `insights`, owned by the `insights`
service. No table here has a foreign key into another schema, and no query here reads another
schema. Everything arrives through events or through the sources' internal export routes.

## 1. Conventions that apply to every table

- **Tenancy.** Every table carries `organization_id uuid NOT NULL`, and every primary key and
  every index leads with it. The one exception is the cross-tenant discovery index on
  `backfill_runs` (§5), which follows the house pattern of ADR-0015 §3.
- **RLS.** `ENABLE` + `FORCE ROW LEVEL SECURITY`, with one policy per table:

  ```sql
  CREATE POLICY tenant_isolation ON insights.<table>
    USING      (organization_id = current_setting('app.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.organization_id', true)::uuid);
  ```

  The GUC is transaction-local (`set_config(..., true)`):
  - the API sets it per request;
  - the consumer sets it per message, from the envelope's `organization_id`;
  - the backfill runner and the sweep set it per organization per transaction.
- **Roles.**
  - `opsgrids_insights` is the NOBYPASSRLS app role, with SELECT/INSERT/UPDATE/DELETE on every
    table here.
  - `opsgrids_outbox` is BYPASSRLS. It gets the scaffold's outbox grants plus **column-level**
    SELECT for discovery only:
    - `org_profile (organization_id, timezone)`;
    - `backfill_runs (id, organization_id, entity, status, next_attempt_at, finished_at)`.

    It cannot read a fact.
  - No other role gets a grant on this schema, and this schema's roles have none elsewhere.
- **Money** is `bigint` minor units beside a `char(3)` ISO currency on the same row. There are no
  floats and no `numeric` money.
- **Fact columns.** Every `*_facts` table also carries:

  | Column | Type | Meaning |
  |---|---|---|
  | `source_created_at` | `timestamptz NOT NULL` | The source row's `created_at`, or the payload's `created_at` where the source can backdate it (deals, companies). Otherwise the envelope's `occurred_at` on the creating event, which is the same instant. |
  | `source_updated_at` | `timestamptz NOT NULL` | **The event version guard.** The envelope `occurred_at` for events, the source row's `updated_at` for exports (§1.1). |
  | `deleted_at` | `timestamptz NULL` | Set from a delete event, a soft-deleted export row, or mark-and-sweep. Every read filters `deleted_at IS NULL`. |
  | `last_seen_run` | `uuid NULL` | The last `backfill_runs.id` that exported this row, for mark-and-sweep |
  | `ingested_at` | `timestamptz NOT NULL DEFAULT now()` | When insights last wrote the row. Load-bearing: the export overwrite rule and mark-and-sweep compare against it (§1.1). |

- **Keys.** A fact's `id` is **the source row's own UUID**, and the key is `(organization_id, id)`.
  Two organizations can never collide, and an id means the same thing in both schemas.

### 1.1 The write rules

Every fact write is one upsert in `app/services.py`. **Events** and **export rows** differ only in
the `WHERE` of the `DO UPDATE` (ADR §2).

```sql
-- From an event: never let an older version overwrite a newer one.
INSERT INTO insights.deal_facts AS f (organization_id, id, ..., source_updated_at)
VALUES (:org, :id, ..., :occurred_at)
ON CONFLICT (organization_id, id) DO UPDATE
   SET ... = EXCLUDED...., source_updated_at = EXCLUDED.source_updated_at, ingested_at = now()
 WHERE f.source_updated_at <= EXCLUDED.source_updated_at;

-- From an export page: a point-in-time read. Overwrite unless an event wrote the fact after
-- this page was requested. :page_requested_at is taken from the database clock just before the
-- HTTP request for the page.
INSERT INTO insights.deal_facts AS f (organization_id, id, ..., source_updated_at, last_seen_run)
VALUES (:org, :id, ..., :row_updated_at, :run)
ON CONFLICT (organization_id, id) DO UPDATE
   SET ... = EXCLUDED...., source_updated_at = EXCLUDED.source_updated_at,
       last_seen_run = EXCLUDED.last_seen_run, ingested_at = now()
 WHERE f.ingested_at < :page_requested_at;
```

When the export `WHERE` declines a row, it still records `last_seen_run` (a separate `UPDATE`),
so mark-and-sweep does not mistake "yielded to a newer event" for "gone at the source".
Mark-and-sweep marks deleted only the facts with
`last_seen_run IS DISTINCT FROM :run AND ingested_at < :run_started_at`.

**Partial events** carry only a transition, not full state. These are:

- `*.deleted`, `purchase_order.{closed,cancelled}`;
- `deal.stage_changed` (it has no `company_id` or `close_date`);
- `enquiry.status_changed`, `leave.cancelled`.

They are applied as `UPDATE ... WHERE source_updated_at <= :version` and **never insert**. If the
row is not there (its creating event was lost), the fold logs `insights.fold.orphan_transition`
and acks. The weekly re-export repairs it. A partial event cannot create a fact whose other
columns would have to be guessed.

## 2. Organization settings

### `org_profile`

| Column | Type | Notes |
|---|---|---|
| `organization_id` | `uuid` PK | |
| `timezone` | `text NOT NULL DEFAULT 'Asia/Kolkata'` | IANA name, from `core.org.*` or the account export |
| `currency` | `char(3) NOT NULL DEFAULT 'INR'` | The organization's currency: what "the" money total means |
| `fiscal_year_start_month` | `smallint NOT NULL DEFAULT 4` | 1–12, which drives `this_quarter` |
| `source_updated_at` | `timestamptz NOT NULL` | Version guard, as for facts |
| `ingested_at` | `timestamptz NOT NULL DEFAULT now()` | |

A fact for an organization with no `org_profile` row is stored normally. Reads fall back to the
defaults above, and every response names the timezone it used.

## 3. Entity facts (slices I2–I7)

Only the entity-specific columns are listed; §1's fact columns come on top. The **owner_ref**
column is, by contract, the value the source's OWN scope compares with the caller's
`subject_ref`, and nothing else.

### `invoice_facts` (finance: invoices and credit notes; I2)

| Column | Type | Notes |
|---|---|---|
| `number` | `text NOT NULL` | Business label |
| `document_type` | `text NOT NULL` | `invoice` \| `credit_note` |
| `status` | `text NOT NULL` | `draft` \| `sent` \| `paid` \| `overdue` \| `void` |
| `issued_date` | `date NOT NULL` | Business date, never converted |
| `due_date` | `date NOT NULL` | |
| `currency` | `char(3) NOT NULL` | |
| `subtotal_minor`, `tax_minor`, `total_minor` | `bigint NOT NULL` | Negative on credit notes. `subtotal_minor` is kept so revenue can move to net of tax without a backfill (ADR Q1). |
| `customer_ref` | `uuid NOT NULL` | Finance customer id. Not used for scope (finance is ORG/ORG). |
| `paid_at` | `timestamptz NULL` | When the settling payment was recorded (`invoice.paid` `occurred_at`; the export's `paid_at`) |

Indexes:

- `(organization_id, issued_date) WHERE status NOT IN ('draft','void')`;
- `(organization_id, source_created_at DESC)`;
- `(organization_id, paid_at DESC) WHERE paid_at IS NOT NULL`.

### `payment_facts` (finance; I2)

Columns:

- `reference text NOT NULL`;
- `received_date date NOT NULL`;
- `method text NOT NULL`;
- `currency char(3) NOT NULL` (derived by finance from the allocated invoices);
- `amount_minor bigint NOT NULL`.

Indexes: `(organization_id, received_date)` and `(organization_id, source_created_at DESC)`.
Payments are append-only at the source, so a fact is written once and only a replay touches it
again.

### `purchase_facts` (finance: purchase book; I2)

Columns:

- `number text NOT NULL`;
- `document_type text NOT NULL` (`invoice` \| `credit_note` \| `debit_note`);
- `document_date date NOT NULL`;
- `currency char(3) NOT NULL`;
- `taxable_minor`, `tax_minor`, `total_minor bigint NOT NULL` (positive; the sign comes from the
  type).

Index: `(organization_id, document_date)`.

### `lead_facts` (crm, lead; I4)

Columns:

- `status text NOT NULL`;
- `source text NOT NULL`;
- `owner_ref uuid NULL` (`leads.owner_id`);
- `converted_at timestamptz NULL`.

Indexes:

- `(organization_id, source_created_at)`;
- `(organization_id, converted_at) WHERE converted_at IS NOT NULL`;
- `(organization_id, owner_ref)`.

No name, email, phone or company text: the lead's person is PII at the source.

### `company_facts` (crm, customer; I4)

Columns:

- `name text NOT NULL` (business label);
- `status text NOT NULL` (`customer` \| `prospect` \| `lead` \| `churned`);
- `owner_ref uuid NOT NULL` (`companies.owner_id`).

Indexes: `(organization_id, status) WHERE deleted_at IS NULL` and
`(organization_id, source_created_at DESC)`.

### `deal_facts` (crm, deal; I3)

| Column | Type | Notes |
|---|---|---|
| `name` | `text NOT NULL` | Business label |
| `stage` | `text NOT NULL` | A stage **key** of the deal's pipeline: `lead` … `closed_lost` for the default six, an organization's own once it can edit them. **No CHECK on its values**, and never the basis of won or lost |
| `stage_outcome` | `text NOT NULL` | `open` \| `won` \| `lost`: the event's `stage_outcome`, or `to_outcome` on a stage change. `CHECK (stage_outcome IN ('open','won','lost'))` |
| `value_minor` | `bigint NOT NULL` | |
| `currency` | `char(3) NOT NULL` | |
| `probability` | `smallint NOT NULL` | 0–100 |
| `owner_ref` | `uuid NOT NULL` | `deals.owner_id` |
| `company_ref` | `uuid NULL` | Null only if a partial event raced ahead (never inserted that way; §1.1) |
| `close_date` | `date NULL` | The *expected* close date, never used as a won date |
| `closed_at` | `timestamptz NULL` | New source column (ADR §3) |

Indexes:

- `(organization_id, closed_at) WHERE closed_at IS NOT NULL`;
- `(organization_id, source_created_at)`;
- `(organization_id, owner_ref)`;
- `(organization_id) WHERE deleted_at IS NULL AND stage_outcome = 'open'`,
  for the open pipeline.

### `purchase_order_facts` (operations; I5)

Columns:

- `number text NOT NULL`;
- `status text NOT NULL`;
- `supplier_ref uuid NOT NULL`;
- `supplier_name text NOT NULL` (business label);
- `owner_ref uuid NULL` (`purchase_orders.raised_by_id`);
- `created_date date NOT NULL`, `expected_date date NOT NULL`, `received_date date NULL`;
- `currency char(3) NOT NULL`;
- `total_minor bigint NOT NULL`.

Indexes:

- `(organization_id, created_date)`;
- `(organization_id, received_date) WHERE received_date IS NOT NULL`;
- `(organization_id, source_created_at DESC)`.

The free-text `reason` on `purchase_order.cancelled` is never stored.

### `task_facts` (projects; I6)

Columns:

- `project_ref uuid NOT NULL`;
- `title text NOT NULL` (business label);
- `stage text NOT NULL` (`backlog` \| `in_progress` \| `review` \| `done`);
- `priority text NOT NULL`;
- `points integer NOT NULL`;
- `due_date date NULL`;
- `owner_ref uuid NULL` (`tasks.assignee_id`, a delivery member id);
- `completed_at timestamptz NULL` (new source column, ADR §3).

Indexes:

- `(organization_id, completed_at) WHERE completed_at IS NOT NULL`;
- `(organization_id, source_created_at)`;
- `(organization_id, owner_ref) WHERE deleted_at IS NULL AND stage <> 'done'`.

### `enquiry_facts` and `enquiry_line_facts` (storefront; I7)

`enquiry_facts` columns:

- `store_ref uuid NOT NULL`;
- `company text NOT NULL` (the buyer's business name, as the source shows it to `enquiry.read`);
- `status text NOT NULL` (`new` \| `contacted` \| `won` \| `lost`);
- `line_count integer NOT NULL`.

Its index is `(organization_id, source_created_at DESC)`. The buyer's name, email, phone, GSTIN
and requirement text are never stored.

`enquiry_line_facts` is keyed `(organization_id, enquiry_id, line_no)`, with an in-schema FK to
`enquiry_facts` `ON DELETE CASCADE`. Its columns:

- `product_ref uuid NOT NULL`;
- `product_name text NOT NULL` (the catalogue name as it was when the enquiry was sent);
- `quantity integer NOT NULL`.

Its index is `(organization_id, product_ref)`. Lines are written once with the enquiry, because an
enquiry is append-only at the source.

## 4. HR facts (slice I9, optional)

Rows carry **no name, employee number, location or reason**:

- **`employee_facts`**:
  - `status text NOT NULL`;
  - `joined_on date NULL`, `exited_on date NULL`.
- **`attendance_facts`** (one row per attendance record):
  - `employee_ref uuid NOT NULL`;
  - `work_date date NOT NULL`;
  - `status text NOT NULL`.

  Index `(organization_id, work_date)`.
- **`leave_facts`**:
  - `employee_ref uuid NOT NULL`, `leave_type_ref uuid NOT NULL`;
  - `start_date date NOT NULL`, `end_date date NOT NULL`;
  - `half_day_start boolean`, `half_day_end boolean`;
  - `total_half_days integer NOT NULL`;
  - `status text NOT NULL`.

  Index `(organization_id, start_date, end_date) WHERE status = 'approved'`.

## 5. Captured state, coverage and runs

### `daily_snapshots`

| Column | Type | Notes |
|---|---|---|
| `organization_id` | `uuid NOT NULL` | |
| `metric` | `text NOT NULL` | `pipeline_value`, `open_deals`, `weighted_pipeline`, `customers`, `open_tasks`, `stock_value`, `low_stock_items`, `out_of_stock_items` (I9 adds none: headcount derives from dates) |
| `dimension` | `text NOT NULL DEFAULT ''` | The currency code for money metrics, so each currency has its own row and is never summed. `''` otherwise. |
| `day` | `date NOT NULL` | The organization-local date the capture belongs to |
| `value` | `bigint NOT NULL` | A count, or minor units |
| `currency` | `char(3) NULL` | Set for money metrics (equals `dimension`) |
| `captured_at` | `timestamptz NOT NULL` | The last capture of this day |

The primary key is `(organization_id, metric, dimension, day)`.

- **Written only by the hourly sweep**, and only for the organization's **current local day**. A
  row whose `day` has ended is never updated again. This is an application rule with a test, not a
  trigger, because "has ended" depends on the organization's timezone.
- Captures are ORG-grain. There is no owner dimension, so history can never be sliced to a person
  (ADR §4).
- **Not rebuildable** (ADR §2). A rebuild never truncates this table.

### `metric_coverage`

`(organization_id, metric)` is the primary key, with these columns:

- `history_from date NULL`: `NULL` means complete over the source's whole history;
- `basis text NOT NULL`: `source_dates` \| `source_column_from_go_live` \| `first_capture`;
- `computed_at timestamptz NOT NULL`.

It is recomputed after every completed backfill run for the entity the metric reads, and by the
sweep when a snapshot metric's first capture lands.

### `backfill_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | Also written to each exported fact's `last_seen_run` |
| `organization_id` | `uuid NOT NULL` | |
| `entity` | `text NOT NULL` | `account.organizations`, `finance.invoices`, `finance.payments`, `finance.purchases`, `lead.leads`, `customer.companies`, `deal.deals`, `operations.purchase_orders`, `projects.tasks`, `storefront.enquiries` (+ I9: `employee.employees`, `attendance.records`, `leave.requests`) |
| `kind` | `text NOT NULL` | `initial` \| `reconcile` \| `manual` |
| `status` | `text NOT NULL` | `pending` \| `running` \| `done` \| `failed` |
| `cursor` | `jsonb NULL` | `{"after_updated_at": …, "after_id": …}`, advanced in the same transaction as the page's upserts |
| `rows_upserted`, `rows_swept` | `integer NOT NULL DEFAULT 0` | |
| `attempts` | `integer NOT NULL DEFAULT 0` | Max 5, then left `failed` for a person |
| `next_attempt_at` | `timestamptz NOT NULL` | |
| `last_error` | `text NULL` | Transport and status only, never a response body |
| `started_at`, `finished_at` | `timestamptz NULL` | |

Indexes:

- `(organization_id, entity, finished_at DESC)`;
- **`(status, next_attempt_at)`** for cross-tenant discovery on `opsgrids_outbox`. It is the one
  index that does not lead with `organization_id`, like approval's SLA-sweep index, because
  discovery has no tenant to bind while it asks.

`processed_events`, `outbox` and `audit_logs` come from the scaffold, unchanged:

- `processed_events` has no `organization_id`, because it is the platform mixin every consumer
  uses (ADR risk 8).
- `outbox` stays empty, because insights publishes nothing. It is kept for `/health/outbox`
  parity and listed in `EDGE_RELAY_SCHEMAS` like console.
- `audit_logs` stays empty, because insights has no user-initiated write.

## 6. Retention

| Data | Kept | Removed by |
|---|---|---|
| Facts | While the source row lives | Soft-deleted facts are purged 90 days after `deleted_at` (daily, in the sweep) |
| `daily_snapshots` | 26 months: enough for a 12-month series and a year-over-year comparison | Daily purge in the sweep |
| `backfill_runs` | 90 days, always keeping the latest per (organization, entity) | Daily purge |
| `processed_events` | 30 days. The version guard, not this ledger, is what keeps a late replay harmless. | Daily purge |
| `org_profile`, `metric_coverage` | Live | n/a |
| A departed organization | Until the platform has an erasure policy (none exists). Every table leads with `organization_id`, so erasure is one `DELETE` per table when it does. | n/a |

## 7. Sizes (for the capacity model)

| Table | Rows per typical org per month | Largest realistic org per month |
|---|---|---|
| `invoice_facts` + `payment_facts` + `purchase_facts` | ~600 | ~9,000 |
| `lead_facts` + `company_facts` + `deal_facts` | ~400 | ~4,000 |
| `purchase_order_facts` | ~50 | ~900 |
| `task_facts` | ~800 | ~8,500 |
| `enquiry_facts` + lines | ~150 | ~4,000 lines |
| `daily_snapshots` | ~300 | ~300 (per metric, not per entity) |

At roughly 400–500 bytes per row including indexes, that is ~1 MB a month for a typical
organization and ~12 MB a month for the largest.
