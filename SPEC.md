# SPEC.md — OpsGrids technical specification

The source of truth for **what the platform is, how it is put together, and what it exposes**.
It is a map, not a copy: each section names the authoritative file, and where this page and the
code disagree, the code wins and this page is the bug.

| For | Read |
|---|---|
| Rules that cause data corruption if broken | [`CLAUDE.md`](CLAUDE.md) |
| Why it is shaped this way | [`docs/adr/`](docs/adr/) — start with ADR-0000 |
| Decisions made, bugs fixed, lessons | [`MEMORY.md`](MEMORY.md) |
| What is in flight right now | [`HANDOVER.md`](HANDOVER.md) |
| Long-form architecture | [`docs/architecture.md`](docs/architecture.md) (sections 2, 10, 11 and 19 predate later services; prefer this page's inventory) |

Last verified against `main` @ `d73bb9e4` (2026-10-14).

---

## 1. Product

**One organization → one account → one user directory → many products.** A customer signs up once,
then activates products against the same organization, people and permissions. Nothing is
re-uploaded or migrated when a product is added.

| Product key | Sold as | Services | App | Prod host |
|---|---|---|---|---|
| `core` | Platform (always on) | `account`, `approval`, `document` (manifest sources); plus `auth`, `product` | `opsgrids-dashboard` | `app.opsgrids.com` |
| `hrms` | People | `employee`, `attendance`, `leave` | `product-hrms` | `people.opsgrids.com` |
| `crm` | Revenue | `customer`, `deal`, `lead` | `product-crm` | `revenue.opsgrids.com` |
| `pulseboard` | PulseBoard | `project`, `timesheet`, `standup` | `product-pulseboard` | `pulse.opsgrids.com` |
| `projects` | Projects | `projects` | `product-projects` | `projects.opsgrids.com` |
| `support` | Support | `support` | `product-support` | `support.opsgrids.com` |
| `marketing` | Marketing | `marketing` | `product-marketing` | `marketing.opsgrids.com` |
| `engage` | Engage (email + WhatsApp) | `notification` | `product-engage` | `engage.opsgrids.com` |
| `finance` | Finance & GST | `finance` | `product-finance` | `finance.opsgrids.com` |
| `itsm` | IT Management | `itsm` | `product-itsm` | `it.opsgrids.com` |
| `operations` | Inventory & manufacturing | `operations` | `product-operations`, `worker-app` | `ops.opsgrids.com`, `worker.opsgrids.com` |
| `storefront` | Storefront | `storefront` | `product-storefront` + buyer bundle | `store.opsgrids.com`, `<slug>.stores.opsgrids.com` |

Commerce (sold by us, to the customer): `catalog`, `subscription`, `metering`, `billing`,
`payment`. Staff surface: `console` + `opsgrids-internal` at `admin.opsgrids.com`.
Platform: `scheduler` (ADR-0015). Skeletons: `config`, `eventbridge`, `quotation`.

Product-level references: [`docs/crm.md`](docs/crm.md), [`docs/inventory/`](docs/inventory/),
[`docs/fulfilment/`](docs/fulfilment/), [`docs/whatsapp/`](docs/whatsapp/),
[`docs/insights/`](docs/insights/), [`docs/commerce-packaging.md`](docs/commerce-packaging.md).

## 2. System shape

```
browser SPAs ──▶ Caddy (TLS, static bundles, CSP) ──▶ app-gateway ──┐
staff console ─────────────────────────────────────▶ admin-gateway ─┤  verify JWT once, resolve org,
partner webhooks ──────────────────────────────────▶ app-gateway ───┤  check perm version, entitlement,
                                                                     │  rate limit, strip X-* headers
                                                                     ▼
                     34 FastAPI services (one Postgres schema each, own Alembic chain)
                          │ write: state + audit row + outbox row in ONE transaction
                          ▼
                     edge-relay ──▶ RabbitMQ ──▶ consumers (idempotent, processed_events ledger)
     PostgreSQL 16 + PostGIS · Redis · RabbitMQ · MinIO (S3) · SMTP (Google Workspace)
```

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, aio-pika, redis-py, structlog, httpx |
| Auth | EdDSA (Ed25519) JWTs, JWKS; argon2 passwords; TOTP MFA + step-up (ADR-0023) |
| Frontend | Node ≥22, pnpm workspace, React 19, react-router 7, Vite; shared `opsgrids-ui`, `opsgrids-client`, `opsgrids-config` |
| Packaging | `uv` workspace, `uv.lock`; one image per service |
| Production | One Azure VM, Docker Compose, Caddy — `infrastructure/edge/` (ADR-0012, ADR-0013) |

## 3. Request path

Gateway checks in order (cheapest and least leaky first):

| # | Check | Failure |
|---|---|---|
| 1 | Route exists | `404` |
| 2 | Token signature, issuer, audience, expiry, type | `401` |
| 3 | Token carries an organization | `401` |
| 4 | Permission version current (Redis) | `401 token-stale` |
| 5 | Product entitled | `402` (not bought ≠ not allowed) |
| 6 | Rate limit, per caller | `429` |
| 7 | Proxy upstream with internal token + `X-*` identity headers | — |

Errors are RFC 7807 `problem+json`. The SPA keeps the access token in memory only; the refresh token
is an httpOnly cookie.

## 4. Tenancy and authorization

- Every tenant table extends `TenantBase` (`id`, `organization_id`, `created_at`, `updated_at`,
  `deleted_at`); every index leads with `organization_id`.
- The request transaction sets `app.organization_id` with `set_config(…, true)`; forced RLS
  policies (ADR-0002) are the net, the selector filter (`ctx.apply()`) is the control.
- Permission grammar: `product.module.resource.action:SCOPE`, scope ladder
  `OWN < TEAM < LOCATION < ORG`; widest grant wins. Manifests are generated into
  `libs/contracts/permissions/<product>.json`.
- A product's admin role is built from its own catalogue. New codes reach live orgs only through
  `make edge-reconcile PRODUCTS=…` (ADR-0007).
- PII is sealed and masked; reveal needs a `read_sensitive` permission, MFA step-up, and writes an
  audit row (`record_sensitive_read`).

## 5. Public API surface

Production routing is data: [`infrastructure/edge/routes.prod.json`](infrastructure/edge/routes.prod.json)
(app-gateway) and `routes.admin.prod.json` (admin-gateway). Per-service OpenAPI documents are
generated into [`libs/contracts/openapi/`](libs/contracts/openapi/) by `make openapi` and checked by
`make openapi-check` — never hand-edit them. Every prefix below is under `/api/v1`.

| Prefix | Service | Product gate |
|---|---|---|
| `/auth` (`/auth/operator` blocked on app-gateway) | auth | — |
| `/organization`, `/org-units`, `/locations`, `/members`, `/roles`, `/permissions`, `/invitations`, `/domains` | account | — |
| `/approvals` | approval | — |
| `/documents` | document | — |
| `/products`, `/marketplace` | product | — |
| `/plans`, `/catalog` | catalog | — |
| `/subscriptions`, `/subscription` | subscription | — |
| `/usage` | metering | — |
| `/invoices`, `/credit-balance`, `/billing` | billing | — |
| `/payment`, `/webhooks/razorpay`, `/webhooks/razorpay/collections`, `/webhooks/collections` | payment | — |
| `/employees` · `/attendance`, `/shifts`, `/geofences` · `/leave` | employee · attendance · leave | `hrms` |
| `/customers`, `/contacts` · `/deals` · `/leads` | customer · deal · lead | `crm` |
| `/projects` · `/timesheets` · `/standups` | project · timesheet · standup | `pulseboard` |
| `/projects-workspace` | projects | `projects` |
| `/support` | support | `support` |
| `/marketing` | marketing | `marketing` |
| `/engage` | notification | `engage` |
| `/notifications` | notification | `notification` |
| `/webhooks/whatsapp` (+ `/deauthorize`, `/data-deletion`, `/data-deletion-status`) | notification | — |
| `/finance` | finance | `finance` |
| `/itsm` | itsm | `itsm` |
| `/operations` (incl. `/operations/inventory/data/import`) | operations | `operations` |
| `/storefront` · `/storefront/public` | storefront | `storefront` · public |

Service-to-service calls go through the internal client with a per-call internal token; each
internal route is registered (see `docs/crm.md` §7 for the pattern and numbering).

## 6. Events

Envelope (additive-only within a major version):
`event_id, event_type, version, organization_id, aggregate_type, aggregate_id, occurred_at, producer,
correlation_id, causation_id, actor, schema_uri, payload`. Event types read
`<product>.<module>.<resource>.<verb>` (e.g. `crm.pipeline.activity.logged`).

- JSON Schemas live in [`libs/contracts/events/`](libs/contracts/events/); `make contracts-check` proves every emitted type has one.
- One shared `edge-relay` drains every outbox; consumers run inside the API process
  (`RUN_CONSUMERS_IN_API=true`) to fit the memory budget.
- Failures retry, then park in `{queue}.dlq`; inspect and replay with `tools/dlq.py`.

## 7. Data invariants

Schema per service; no cross-schema FKs. Money is integer minor units + ISO currency (rates in
micros, largest-remainder allocation). Invoices, usage events, the credit ledger, refunds and provider
events are append-only, enforced by revoked `UPDATE`/`DELETE` and a finalize-freeze trigger. Plan
versions are immutable; subscriptions pin a version. Commerce split and why: ADR-0004.

## 8. Quality gate

`make check` (lint + typecheck + test) is the fast path; `make gate` is what CI runs (adds audit,
licences, secrets, hygiene, import-linter boundaries, OpenAPI/contract drift, migration round-trip,
boot smoke). Frontend: `make frontend-gate`. Tests run on PostgreSQL, never SQLite. Details:
[`docs/TOOLCHAIN.md`](docs/TOOLCHAIN.md), [`docs/testing.md`](docs/testing.md).

## 9. Deployment

One VM (`edge.opsgrids.com`), Compose profiles named after product keys, Caddy for TLS and the
static bundles, nightly `backup.sh` (optional age encryption) and `restore-drill.sh`.
`make deploy` = preflight → build → caddy validate → migrate → up → health. Bundles ship separately
with `make edge-web APP=<app>`, built from the **local checkout**. A memory ceiling
(`EDGE_BUDGET_ACCEPTED_MB`, enforced by preflight from `memory-budget.json`) gates every new
container. Runbook: [`infrastructure/edge/RUNBOOK.md`](infrastructure/edge/RUNBOOK.md); env keys:
[`docs/deploy/`](docs/deploy/).

## 10. Features being built

| Programme | Spec | State |
|---|---|---|
| CRM overhaul (4 sub-projects, 26 releases) | `docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md`, ADR-0038 (D-1…D-41 accepted at defaults) | R1.1, R1.2a/b, R2.1–R2.4, R3.3 merged and live; next R2.5 (stage editing, `crm.pipeline.stage.write`) |
| Order invoicing via CRM identity bridge | ADR-0024/0025, PR #146 | Live in `log_only`; per-org opt-in |
| GST tax invoices | PR #145 | Groundwork, inert until supplier settings exist |
| Inventory & manufacturing | ADR-0014, [`docs/inventory/plan.md`](docs/inventory/plan.md) | Phases 0–9 live |
| Insights projection | ADR-0020 | Built on `sprint/insights*` branches (+99 MB), not deployable until the owner raises the memory ceiling |

Open product questions: [`docs/adr/open-questions-register.md`](docs/adr/open-questions-register.md).
Backlog: [`docs/backlog.md`](docs/backlog.md).
