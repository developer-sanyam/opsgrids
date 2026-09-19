# Features

What the platform can do today, what half-works, and what is only a decision on paper.

Legend: **Built** — works and is tested · **Partial** — exists, not wired end to end ·
**Absent** — planned or decided, not written.

> Read this with `docs/what-was-built.md`. This file is the capability list; that one is the
> trust level. A row marked Built means the code exists and its tests pass — not that a customer
> has ever used it.

---

## The shape of the claim

Nine products are defined in the marketing catalogue
(`frontend/opsgrids-website/src/content/products.ts`, the single source of truth for the site).
**All nine are `status: 'soon'`; none is `available` or `beta`.** Behind them, 23 services carry
real schemas, routes and row-level security, and nine product SPAs are built.

The gap that matters: **every product SPA renders from fixtures, not from the API.** Each app has
a typed client with the live gateway paths already wired, defaulting to `USE_FIXTURES` and falling
back to `fixtures.ts`. Going live is a flag flip per app (`VITE_USE_FIXTURES=false`) — but until
that flip is made and proven, no product is end-to-end.

---

## Identity and access

| Capability | State | Notes |
|---|---|---|
| Create an organization | Built | Also seeds the permission catalogue and default roles |
| Departments, locations | Built | Department tree stored as a hierarchy and read as one; `POST /org-units/{id}/move` reparents |
| Global user, per-org membership | Built | One person, many organizations, one login |
| Invite, accept, revoke | Built | Accepting is a **public** route — the token is the credential |
| Roles and permissions | Built | Grammar is `product.module.resource.action:SCOPE` |
| Scope ladder `OWN < TEAM < LOCATION < ORG` | Built | Widest scope wins when two roles overlap |
| Effective grants for a membership | Built | `GET /memberships/{id}/grants` |
| Sign in, sign out | Built | |
| Choose and switch organization | Built | New token, same session |
| Refresh-token rotation with reuse detection | Built | Reusing a rotated token kills the family |
| TOTP MFA — enrol, confirm, verify | Built | Step-replay protected; recovery codes issued |
| Password change, forgot, reset | Built | |
| Per-org SSO | **Partial** | `/auth/sso/start`, `/auth/sso/callback` and `/auth/sso/config` exist over `org_idp_configs` and `external_identities`; treat the provider flow as unproven until an IdP is tested against it |
| Operator (staff) login | Built | Separate `/auth/operator/*` routes and `staff_accounts` table |
| Revocation takes effect immediately | Built | Permission version bumps on all four grant-changing paths |

## Tenancy and isolation

| Capability | State | Notes |
|---|---|---|
| `organization_id` on every tenant row | Built | |
| Every query filtered by organization | Built | Via selectors, by convention |
| **Row-level security** | **Built** | Policies ship in **23 services** — account, attendance, auth, billing, catalog, console, customer, deal, employee, finance, itsm, marketing, metering, notification, operations, payment, product, project, projects, standup, subscription, support, timesheet |
| Non-superuser application role | Built | Per-service NOBYPASSRLS roles; separate owner and directory roles |
| Transaction-local tenant GUC | Built | Per-connection would be a cross-tenant leak — the pool reuses connections |
| Cross-tenant isolation test suite | Partial | Storage layer proven; the generated per-route sweep is still to write |

A forgotten `WHERE organization_id = …` returns nothing rather than another tenant's rows.

## Gateways

| Capability | State |
|---|---|
| Route table as data, per gateway | Built |
| Token verification per audience | Built |
| Identity-header stripping | Built |
| Permission resolution, cached against the version | Built |
| Product entitlement gate (402) | **Built** — `product-service` now serves `GET /organizations/{id}/entitlements` behind it |
| Rate limiting, fail-closed | Built |
| Staff-only routes answering 404 | Built |

---

## Products

The previous version of this file said "**None of them exist.**" That is no longer true. Each
product below has a schema, routes and RLS behind it, and a built SPA in front of it rendering
from fixtures.

### HRMS — People

| Capability | State | Notes |
|---|---|---|
| Employee records | Built | `employees`; create, read, patch, list |
| Employee lifecycle transitions | Built | `POST /employees/{id}/transition` |
| PII encryption and masking | Built | `employee_pii` is separate; `POST /employees/{id}/pii/unmask` needs its own permission and writes an audit row |
| Reporting lines | Built | `GET /employees/{id}/reports` |
| Attendance check-in / check-out | Built | `attendance_records` |
| Shifts | Built | `shifts`; create, patch, list |
| Geo-fenced punches | Built | `geofences` table with PostGIS |
| Attendance regularization | Built | `POST /attendance/{id}/regularize` — a correction, not a rewrite |
| Leave ledger and balances | **Absent** | The pitch promises a ledger-derived balance; no leave tables exist |
| Payroll | **Absent** | No payroll tables, no statutory rate versioning |

### CRM and Sales CRM — Revenue

| Capability | State | Notes |
|---|---|---|
| Companies, contacts, owners | Built | `companies`, `contacts`, `owners` |
| Contact phone — set and reveal | Built | `POST /contacts/{id}/phone:reveal` is the audited unmask path |
| Messaging target resolution | Built | Feeds WhatsApp — `GET /…/contacts/{id}/messaging-target` |
| Deals and activities | Built | `deals`, `activities`; list, summary, detail |
| Pipeline stages as a service | **Absent** | `pipeline`, `lead` and `quotation` are 229-line scaffolds — one `/info` route each |

### PulseBoard and Project Management — Work

| Capability | State | Notes |
|---|---|---|
| Projects, tasks, people (PulseBoard) | Built | `project` service — `projects`, `tasks`, `people` |
| Project workspace — projects, milestones, tasks | Built | `projects` service, `/projects-workspace/*` |
| Board and timeline views | Built | `GET /projects-workspace/board`, `/timeline` |
| Timesheets | Built | `timesheets`, `timesheet_entries`; summary, list, upsert |
| Standups | Built | `standup_entries`; list and create |
| Capacity planning, portfolio reporting | Absent | Sold on the Business tier |

### Support & IT — Operations

| Capability | State | Notes |
|---|---|---|
| Tickets, replies, assignment | Built | `tickets`, `ticket_messages` |
| Agents | Built | `agents` |
| SLA policies | Built | `sla_policies`; `GET /support/sla-policy` |
| Incidents and worklogs (ITSM) | Built | `incidents`, `incident_worklogs` |
| Service requests and service catalogue | Built | `requests`, `catalog_items` |
| Asset register | Built | `assets`; list, create, lookup by tag |
| Customer portal, automation | Absent | Sold on the Business tier |

### Marketing — Revenue

| Capability | State | Notes |
|---|---|---|
| Campaigns | Built | `campaigns`; list, create, detail |
| Campaign performance | Built | `GET /marketing/campaigns/{id}/performance` |
| Audiences | Built | `audiences`; list, create |
| Revenue-vs-spend attribution | Built | `revenue_spend_points`; `GET /marketing/analytics/revenue-vs-spend` |
| Channels overview | Built | `GET /marketing/channels` |
| **Storefront** | **Absent** | There is no storefront anywhere in the codebase — no public catalogue, cart, checkout or published landing pages. Marketing is campaign management and attribution only |
| Content approvals, goal tracking | Absent | Named in the plan tiers |

### Finance — Finance

The largest service in the platform (~19k lines, 28 routes), and the only one with deep
India-specific statutory machinery.

| Capability | State | Notes |
|---|---|---|
| Customers, invoices, invoice lines | Built | |
| Payments and allocations | Built | `payments`, `payment_allocations` |
| Tax rates | Built | `tax_rates` |
| GST registration | Built | `gst_registrations`; get and put |
| GST returns, transitions, diff | Built | `gst_returns`, `gst_return_transitions`; `GET /finance/gst/returns/{id}/diff` |
| E-invoicing (IRN) | Built | `einvoice_filings`; file and cancel |
| E-way bills | Built | `eway_bills`; generate and cancel |
| IMS — supplier document reconciliation | Built | `ims_documents`, `ims_reconciliations`, `ims_decisions`; `POST /ims/sync` |
| Withholding (TDS) | Built | `withholding_rates`, `invoice_withholdings`; summary endpoint |
| Ledger and overview | Built | `GET /finance/ledger`, `/finance/overview` |
| Multiple legal entities | Absent | Sold on Enterprise |

### Operations — inventory and procurement

Sold as "automation and reporting", but what is actually built is **inventory and procurement**.

| Capability | State | Notes |
|---|---|---|
| **Inventory items** | **Built** | `inventory_items` — SKU, warehouse, on-hand, **reorder point**, UoM, unit value in minor units |
| **Warehouses** | **Built** | `warehouses`; `GET /operations/warehouses` |
| **Suppliers** | **Built** | `suppliers` — lead time, on-time %, spend, open POs, contact, location |
| **Purchase orders** | **Built** | `purchase_orders` + `purchase_order_lines` — draft→received status, expected vs received dates |
| **Goods receipt** | **Built** | `POST /operations/purchase-orders/{id}/receive` posts received quantities against lines |
| Operations overview | Built | `GET /operations/overview` |
| Automation rules, bulk operations | **Absent** | This is what the tier actually sells; none of it is written |
| Cross-product reporting | Absent | |

---

## Commerce

| Capability | State | Notes |
|---|---|---|
| Plans and immutable plan versions | Built | `plans`, `plan_versions`; publish and retire |
| Prices, features, seat classes | Built | `prices`, `features`, `seat_classes` |
| Subscriptions | Built | Create, list, detail, **change-plan**, suspend, cancel |
| Subscription transitions | Built | `subscription_transitions` — the audit trail of state changes |
| Dunning cycles | Built | `dunning_cycles`; see ADR-0005 |
| Invoices — finalize, void | Built | `invoices`, `invoice_lines`; append-only |
| Credit ledger | Built | Grant, consume, reverse, balance — append-only, corrections are reversing rows |
| Usage metering | Built | `usage_events`, `meters`, `usage_rollups`; `POST /usage` |
| Payment charges and mandates | Built | `mandates`, `charges`, `refunds` |
| **Razorpay webhook** | Built | `POST /razorpay`; `provider_events` for idempotency |
| Stripe adapter | Absent | Razorpay only |
| Money primitives | Built | `platform_core.money` — integer minor units, ISO currency, rates in micros, largest-remainder allocation |
| Operator console | Built | `console` — organizations, subscriptions, usage, audit, `POST /admin/overrides` |

## Platform services

| Service | State | Notes |
|---|---|---|
| **notification** | **Built** | Templates, provider templates, rules, messages, deliveries, per-user preferences, subscribable-event discovery |
| **WhatsApp (Cloud API)** | **Built** | `GET /whatsapp` webhook verification, `POST /whatsapp` inbound; template send is Phase 1 and the API version is pinned |
| `document` | **Absent** | 229-line scaffold |
| `scheduler` | **Absent** | 229-line scaffold |
| `eventbridge` | **Absent** | 229-line scaffold |
| `config` | **Absent** | 229-line scaffold |
| `activity` | **Absent** | 229-line scaffold |
| `lead`, `pipeline`, `quotation` | **Absent** | 229-line scaffolds |
| `product` | Built | Products, activations, entitlements |
| Search, realtime, workflow, analytics, bulk | Absent | Named in the README diagram; no directories exist |

## Events

| Capability | State | Notes |
|---|---|---|
| Transactional outbox | Built | Every service |
| Relay draining with `FOR UPDATE SKIP LOCKED` | Built | All 31 services carry `relay.py` |
| Idempotent consumer base, `processed_events` ledger | Built | |
| **Consumers actually handling events** | **Partial** | No longer zero. Real handlers in `account`, `console`, `notification`, `finance`, `payment`, `product` and `subscription`. The other 24 services still ship the 29-line stub |
| Enqueue only after commit | Built | Via the outbox, never inline |

The previous version of this file said "zero handlers registered". Seven services now consume.

## Frontend

| Surface | State | Notes |
|---|---|---|
| Nine product SPAs | **Partial** | Built and navigable; **all render from `fixtures.ts`** |
| Typed API clients with live paths | Built | `USE_FIXTURES` guards the switch; dev-only by construction, so fixture mode cannot ship to production |
| Marketing website | Built | Index, product pages, pricing and sitemap all generated from one catalogue file |
| Shared UI library (`opsgrids-ui`) | Built | |
| Admin / internal consoles | Partial | `opsgrids-internal` exists; `atmora-*` directories are pre-rename leftovers |
| **Any SPA talking to a live gateway** | **Absent** | Zero HTTP calls in any product SPA today |

## Observability

| Capability | State |
|---|---|
| Structured logging, correlation IDs | Built |
| Health and readiness endpoints | Built |
| Outbox-lag alerting | **Absent** |
| Metrics, tracing, error tracking | Absent |

A stalled relay is still invisible: it logs once at startup, then sleeps in a loop finding
nothing, and the process looks healthy while the outbox grows without bound.

---

## The honest summary

- **Backends are real** — 23 services with schemas, routes, RLS and audit.
- **Frontends are real but unplugged** — nine SPAs, all on fixtures, zero live calls.
- **Money is the deepest part** — catalogue, subscriptions, invoices, credit, metering, Razorpay,
  and a GST/e-invoice/e-way/TDS stack in `finance`.
- **The named gaps are leave, payroll, storefront, automation rules, and the eight scaffolds**
  (`activity`, `config`, `document`, `eventbridge`, `lead`, `pipeline`, `quotation`, `scheduler`).
- **Nothing is sellable** — all nine products are `soon`, and the entitlement path has never been
  exercised by a paying customer.
