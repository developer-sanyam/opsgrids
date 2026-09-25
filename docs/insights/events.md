# Insights: event contracts

Part of [ADR-0020](../adr/0020-insights-projection.md). Every schema named here is committed at
`libs/contracts/events/<routing key>/v1.schema.json`. A schema whose producer does not yet emit
every field says so in its `$comment`, naming the fields and the function that must add them.

## 1. How insights consumes

- **Queue and bindings.** One queue, `insights.events` (DLQ `insights.events.dlq`), on the
  `opsgrids.events` topic exchange, prefetch 16, run by the platform's `EventConsumer` inside the
  API process on a bulkhead engine. Each routing key in §2 is bound by name. There are no
  wildcards, so a new event never lands in insights by accident.
- **One message, one transaction.** Each fold runs in its own transaction:
  1. `session_scope(organization_id=envelope.organization_id)`: the NOBYPASSRLS app role, with
     the tenant GUC set for this message only (the console consumer's shape);
  2. `already_processed`;
  3. the fold;
  4. `mark_processed`, all in one commit.
- **Version guard.** Each fold is an upsert guarded by `source_updated_at <= envelope.occurred_at`
  (data-model.md §1.1), so replays and reordering are harmless. Export pages follow their own
  rule, so a backfill page and an event for the same entity commute.
- **Partial events.** Deletes and bare transitions only `UPDATE`, and never insert
  (data-model.md §1.1).
- **Malformed or off-contract payloads.** A payload missing a field insights needs, or with a
  value outside the schema, **raises**. The message dead-letters to `insights.events.dlq`, where
  it is visible to ops-monitor and replayable with `tools/dlq.py`. Nothing is silently dropped.
  The entity is repaired by its next event or the weekly re-export.
- **No runtime JSON Schema validation.** `jsonschema` stays a dev dependency. Each producer's own
  test suite validates its real payload against the committed schema (§6), and insights parses
  only the fields it uses, with Pydantic.

## 2. Routing keys insights binds

**Status:**

- **as-is**: emitted today exactly as the schema says.
- **+fields**: emitted today, and the listed fields must be added. The additions are additive
  within v1: existing consumers ignore unknown keys.
- **new**: no emission exists, and the named function must stage it on its own outbox in the same
  transaction as the change.

### Core (account); slice I1

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `core.org.created` | `account/app/services.py:create_organization` | +fields: `fiscal_year_start_month` | `org_profile` |
| `core.org.updated` | `account/app/services.py:update_organization` | +fields: `timezone`, `currency`, `fiscal_year_start_month` (the values after the change; today it carries only `changed`) | `org_profile` |

### Finance; slice I2

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `finance.billing.invoice.issued` | `finance/app/services.py:create_invoice` | +fields: `issued_date`, `due_date`, `subtotal_minor`, `tax_minor` | `invoice_facts`, i.e. `revenue`, `invoices_issued`, feed |
| `finance.billing.invoice.paid` | `finance/app/services.py:record_payment` | +fields: `currency`, `paid_date` | `invoice_facts.status`, `paid_at`, feed |
| `finance.billing.payment.received` | `finance/app/services.py:record_payment` | +fields: `received_date`, `method`, `currency` (derived from the allocated invoices; refuse mixed currencies) | `payment_facts`, i.e. `payments_received`, feed |
| `finance.billing.purchase.recorded` | `finance/app/services.py:record_purchase_document` | **new** | `purchase_facts`, i.e. `purchases_recorded` |

### CRM: deal; slice I3

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `crm.pipeline.deal.created` | `deal/app/services.py:create_deal` (also reached by lead conversion through deal's internal route) | +fields: `company_id`, `close_date`, `probability`, `closed_at`, `created_at` | `deal_facts` |
| `crm.pipeline.deal.updated` | `deal/app/services.py:update_deal` | +fields: the full state after the patch (`name`, `stage`, `value_minor`, `currency`, `owner_id`, `company_id`, `close_date`, `probability`, `closed_at`). Today it carries only `changed`. | `deal_facts` (a value or owner change moves pipeline and OWN figures) |
| `crm.pipeline.deal.stage_changed` | `deal/app/services.py:move_stage` | +fields: `name`, `value_minor`, `currency`, `owner_id`, `probability`, `closed_at`. `closed_at` needs the new column `deal.deals.closed_at`, set here. | `deal_facts.stage`/`closed_at`, i.e. won/lost, feed |
| `crm.pipeline.deal.deleted` | `deal/app/services.py:delete_deal` | as-is | `deal_facts.deleted_at` |

### CRM: lead and customer; slice I4

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `crm.lead.created` | `lead/app/services.py:create_lead` | +fields: `status` | `lead_facts` |
| `crm.lead.updated` | `lead/app/services.py:update_lead` | +fields: `status`, `source`, `owner_id`, `converted_at` (state after the patch) | `lead_facts` |
| `crm.lead.converted` | `lead/app/services.py:finalize_conversion` | +fields: `owner_id`, `converted_at` | `lead_facts.status`/`converted_at` |
| `crm.book.company.created` | `customer/app/services.py:create_company` | +fields: `created_at` (the function accepts a backdated `created_at`) | `company_facts` |
| `crm.book.company.updated` | `customer/app/services.py:update_company` | +fields: `name`, `status`, `owner_id` (state after the patch) | `company_facts` (a status move to `customer` moves `customers`) |
| `crm.book.company.deleted` | `customer/app/services.py:delete_company` | as-is | `company_facts.deleted_at` |

### Operations: purchase orders; slice I5

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `operations.procurement.purchase_order.raised` | `operations/app/services.py:create_purchase_order` | +fields: `supplier_id`, `supplier_name`, `raised_by_id`, `created_date`, `expected_date`, `currency`, `total_minor` (the sum of line amounts) | `purchase_order_facts` |
| `operations.procurement.purchase_order.updated` | `operations/app/services.py:update_purchase_order` | +fields: the full state after the edit (`number`, `status`, supplier, `raised_by_id`, dates, `currency`, `total_minor`). Today it carries only `changed`. | `purchase_order_facts` |
| `operations.procurement.purchase_order.approved` | `operations/app/services.py:approve_purchase_order` | +fields: `currency`, `total_minor` | status |
| `operations.procurement.purchase_order.received` | `operations/app/services.py:receive_purchase_order` | +fields: `expected_date` | `received_date`, i.e. `on_time_delivery` |
| `operations.procurement.purchase_order.closed` | `operations/app/services.py:close_purchase_order` | as-is | status |
| `operations.procurement.purchase_order.cancelled` | `operations/app/services.py:cancel_purchase_order` | as-is (insights drops `reason`) | status |
| `operations.procurement.purchase_order.deleted` | `operations/app/services.py:delete_purchase_order` | as-is | `deleted_at` |

### Projects: tasks; slice I6

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `projects.board.task.created` | `projects/app/services.py:create_task` | +fields: `title`, `assignee_id`, `priority`, `due_date`, `points`, `completed_at` | `task_facts` |
| `projects.board.task.updated` | `projects/app/services.py:update_task` | +fields: the same, as state after the change. `completed_at` needs the new column `projects.tasks.completed_at`, set on entering `done` and cleared on leaving. | `task_facts`, i.e. completed, open, overdue, workload |
| `projects.board.task.deleted` | `projects/app/services.py:delete_task` | as-is | `deleted_at` |

### Storefront: enquiries; slice I7

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `storefront.enquiry.created` | `storefront/app/services.py:create_enquiry_for_store` | +fields: `status` (`new`), `lines` (`product_id`, `name`, `quantity` per line: the same list the row stores) | `enquiry_facts`, `enquiry_line_facts` |
| `storefront.enquiry.status_changed` | `storefront/app/services.py:update_enquiry_status` | **new**. Today the transition writes an audit row and no event. | `enquiry_facts.status` |

The storefront keys keep their existing three-part shape (`storefront.enquiry.*`) rather than the
four-part grammar. Renaming a published routing key would break its binding, and nothing gains
from it.

### HR; slice I9, optional

| Routing key | Producer | Status | Insights uses it for |
|---|---|---|---|
| `hrms.people.employee.onboarded` | `employee/app/services.py:create_employee` | +fields: `date_of_joining`, `date_of_exit` | `employee_facts`, i.e. `headcount` |
| `hrms.people.employee.updated` | `employee/app/services.py:update_employee` | +fields: the same | same |
| `hrms.people.employee.transitioned` | `employee/app/services.py:transition_status` | +fields: the same | same |
| `hrms.time.attendance.checked_in` | `attendance/app/services.py:check_in` | as-is | `attendance_facts`, i.e. `checked_in` |
| `hrms.time.attendance.regularized` | `attendance/app/services.py:regularize_attendance`, `apply_regularization_decision` | +fields: `work_date` | `attendance_facts.status` |
| `hrms.time.leave.requested` | `leave/app/services.py:apply_for_leave` | as-is (insights drops `reason`) | `leave_facts` (pending) |
| `hrms.time.leave.applied` | `leave/app/services.py:_publish_leave_applied` | +fields: `status`, `employee_id`, `leave_type_id`, `start_date`, `end_date`, `total_half_days` | `leave_facts.status`, i.e. `on_leave` |
| `hrms.time.leave.cancelled` | `leave/app/services.py:cancel_leave_request` | as-is | `leave_facts.status` |

`hrms.time.attendance.checked_out` (`attendance/app/services.py:check_out`, as-is) has a committed
schema so the attendance record's contract is whole, but insights does not bind it.

**Counts.** 28 keys bound in I1–I7, and 8 more in I9. There are 37 schemas: 10 as-is, 25 needing
fields, and 2 new emissions. Approval, leave and attendance already consume several of the HR
keys; these schemas are the first written contract for traffic that exists today.

## 3. Events insights deliberately does not consume

| Routing keys | Why not |
|---|---|
| `operations.inventory.stock.{posted,reserved,released}`, `operations.inventory.item.*` | Stock value is **pulled** hourly from operations' own valuation (ADR §5). Copying the stock ledger would duplicate ADR-0014's rounding rule and the platform's highest-volume stream. There are no schemas for these in this change, because no consumer is committing to them. |
| `crm.pipeline.activity.logged`, `crm.book.contact.*`, `crm.lead.conversion_aborted` | Activity bodies and contacts are person-level. No dashboard metric needs them. |
| `commerce.*` | Operator commerce. Console owns MRR and its history (ADR §10, ADR-0003). |
| `projects.portfolio.project.*`, `projects.delivery.milestone.*`, `projects.team.member.*` | Project health, milestones and members are current-state, and the source serves them already. |
| `support.*`, `itsm.*`, `marketing.*`, `pulseboard.*`, `engage.*` | Out of v1 (ADR non-goals). Their overviews are real today, and they join by the same pattern when a dashboard needs history. |
| `core.membership.*`, `core.grants.changed` | Authorization is read per request from the forwarded grants, never projected. |

## 4. Fields insights must not store, even when an event carries them

| Event | Field | Reason |
|---|---|---|
| `hrms.people.employee.*` | `display_name`, `employee_no`, `location_code`, managers, `pii_fields` | Person-identifying. Headcount needs only dates and status. |
| `hrms.time.leave.requested` | `reason` | Free text written by the employee |
| `hrms.time.attendance.checked_in` | `location_code`, `work_mode` | Not needed, and location is personal data |
| `operations.procurement.purchase_order.cancelled` | `reason` | Free text |
| `finance.billing.invoice.issued` | `customer_id` is kept as an opaque ref; `supply_type`, `place_of_supply`, `requires_einvoice` are not stored | Not needed |
| `crm.lead.*` | Everything but status, source, owner and dates. The lead's person is PII at the source. | |
| `storefront.enquiry.created` | Nothing beyond the listed fields. The buyer's email and phone are sealed at the source and never travel on the event. | |

## 5. Producer findings from this design (each fixed in its slice)

1. **Payments have no currency** (`finance.payments`: `amount_minor` alone), contrary to
   ADR-0000 §8. I2 derives the currency from the allocated invoices and refuses a mixed-currency
   allocation. Adding a column is finance's own call.
2. **`/deals/summary` sums `value_minor` across currencies** (`deal/app/domain.py:
   pipeline_value_minor`). Insights splits by currency. The deal-side fix is to filter to the
   organization's currency and report the rest. This is small, but it is not an insights slice.
3. **Finance's "this month" is UTC** (`finance/app/selectors.py:_today`). Its docstring says no
   organization timezone exists, which stopped being true when account gained
   `organizations.timezone`. Finance can learn it by consuming `core.org.*` (the schemas are
   committed here). That is a finance follow-up. Insights does not depend on it, because a card
   takes value and delta from one source.
4. **`receive_purchase_order` stamps `updated_at` in Python** (`datetime.now(UTC)`), not with
   the transaction's `now()`, so the row's version and its event's `occurred_at` differ by
   microseconds. Nothing relies on them being equal. Events are guarded by event versions, and
   export rows by `ingested_at` (data-model.md §1.1). It is recorded so nobody "fixes" either rule
   to compare the two.
5. **Seeded or backdated rows have no audit trail**, which is why the `closed_at` and
   `completed_at` backfills fall back to `NULL` and to go-live coverage rather than guessing.

## 6. The test each producing slice adds

For every routing key in §2 marked **+fields** or **new**, the producing service's test suite:

1. drives the real write path (the service function the table names) inside a real transaction;
2. reads the staged `outbox` row;
3. calls `platform_core.events.validate_event_schema(event_type, 1, payload,
   contracts_root=…)`.

This is exactly the shape of `services/notification/tests/test_event_contracts.py`. Once it
passes, the schema's `$comment` is removed in the same commit, because the schema is no longer
ahead of its producer. `libs/contracts/tests/test_event_schemas.py` gains one assertion per
producer, as it has for `crm.lead.conversion_aborted`: the `EVT_*` constant the service stages
under has a committed schema.
