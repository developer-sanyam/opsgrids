# Architecture

The narrative version, describing **what is in the code today** rather than what is planned. For
why each decision was taken, see [decisions.md](decisions.md) and the ADRs. For what is still
missing, see [backlog.md](backlog.md) and [production-readiness.md](production-readiness.md).

> Where this document and the code disagree, the code is right and this document is a bug.

---

## 1. What OpsGrids is

One organization → one account → one user directory → many products.

CRM, HRMS, PulseBoard, Finance, ITSM, Operations, Projects, Marketing and Support are **modules of
a single platform**, not separate applications. A customer activates what they need today and adds
more later without a new account, without re-uploading their people, and without migrating
anything.

That sentence is the whole architecture. Everything below is a consequence of it.

## 2. At a glance

| | |
|---|---|
| Backend services | **33 directories** — 22 built out, 9 generated skeletons, 2 gateways |
| Postgres schemas | **31**, one per service; the two gateways own none |
| Frontend workspace | **14 projects** — 9 product apps, 2 consoles, 3 shared packages |
| Python | 3.12, ~838 source files |
| TypeScript | ~395 source files |
| Committed API contracts | 33 OpenAPI documents, 10 product permission manifests |
| Architecture decision records | 6 (ADR-0000 … ADR-0005) |

## 3. Technology stack

### Backend — Python 3.12

| Concern | Choice |
|---|---|
| Web framework | **FastAPI** ≥0.115 on **Uvicorn** (`[standard]`) |
| Validation, settings | **Pydantic v2**, **pydantic-settings** |
| ORM | **SQLAlchemy 2.0** async, **asyncpg** driver |
| Migrations | **Alembic** — one chain per service |
| Messaging | **aio-pika** → RabbitMQ |
| Cache, counters | **redis-py** ≥5.2 |
| Tokens | **PyJWT[crypto]** — EdDSA / Ed25519, JWKS |
| Passwords | **argon2-cffi** |
| Crypto | **cryptography** — signing keys, PII envelope encryption |
| Logging | **structlog**, JSON outside local |
| Service-to-service HTTP | **httpx** |
| Packaging | **uv** workspace, `uv.lock` |

Only `libs/platform-core` is an installable package. Services are **not** installed as packages:
each exposes a top-level `app`, so exactly one can live in a process.

### Frontend — Node ≥22

| Concern | Choice |
|---|---|
| UI | **React 19**, **react-router-dom 7** |
| Language | **TypeScript 5.7**, strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` |
| Build | **Vite 7** for every signed-in app; **Next.js 16** for the marketing site only |
| Styling | **Tailwind CSS v4** via `@tailwindcss/vite`, CSS-first `@theme` tokens |
| Tests | **Vitest 3** |
| Lint | **ESLint 9** flat config, typescript-eslint 8 |
| Package manager | **pnpm** workspace |

**No UI dependency beyond React.** No component library, no chart library, no icon package.
`DataTable`, `Sparkline`, `QrCode` and the icon set are all written in `opsgrids-ui`.

### Data and infrastructure

| Component | Image / version | Local port |
|---|---|---|
| PostgreSQL + PostGIS | `postgis/postgis:16-3.4` | 5434 |
| Redis | `redis:7-alpine` | 6380 |
| RabbitMQ | `rabbitmq:3-management-alpine` | 5673 |
| MinIO (S3) | `minio/minio:latest` | 9100 |
| MailHog (dev SMTP) | `mailhog/mailhog:latest` | 1026 |

Ports are offset from the defaults on purpose so this stack runs beside a legacy one.

Postgres extensions, installed once on a fresh volume: **PostGIS** (geo-fencing is a query, not
app-side maths), `pg_trgm` (name search), `citext` (email is case-insensitive and globally
unique), `btree_gist` (exclusion constraints on overlapping ranges), `uuid-ossp`.

### External integrations

**Razorpay** is the only third-party SDK, in `payment`. The adapter is complete and tested —
signature verification, request/response mapping, the state machine, idempotency — except the
outbound HTTP call, which is stubbed pending live keys. Stripe, Authlib, boto3 and aiosmtplib are
listed in `pyproject.toml` as deliberately deferred until the service needing them exists.

The three GST portals (**IRP**, **IMS**, **e-Way**) have deterministic stubs in `finance`, wired
through `app/deps.py`. Tests assert they open no socket.

---

## 4. The shape

```
CLIENTS     9 product SPAs · operator console · marketing site · mobile · partner APIs
                                     │
                                     │ HTTPS
EDGE        ┌────────────────────────┴────────────────────────┐
            │  app-gateway  :8100      admin-gateway  :8101    │
            │  customer surface        operator + internal     │
            └────────────────────────┬────────────────────────┘
                                     │  verify once · resolve org · gate entitlement
                                     │  strip inbound X-* · mint internal token · proxy
     ┌───────────────┬───────────────┼───────────────┬────────────────┐
     ▼               ▼               ▼               ▼                ▼
 IDENTITY        PEOPLE          REVENUE          WORK             MONEY
 auth    :8110   employee  :8120  customer :8130  project   :8140   billing      :8160
 account :8111   attendance:8121  deal     :8131  timesheet :8141   subscription :8161
 product :8112                    marketing:8134  standup   :8142   metering     :8162
                                  support  :8180  quotation :8143   catalog      :8164
                                  lead     :8135  projects  :8170   payment    (no route)
                                  pipeline :8136  finance   :8171
                                  activity :8137  operations:8172
                                                  itsm      :8173
                                     │
 PLATFORM   document :8150 · notification :8151 · approval :8152 · eventbridge :8163 (admin only)
            config · scheduler — no gateway prefix, service-to-service only
 OPERATOR   console :8170, behind admin-gateway
                                     │
DATA        PostgreSQL 16 + PostGIS — one schema per service, organization_id on every row
            Redis (permission version, counters) · RabbitMQ (domain events) · MinIO (objects)
```

Ports above are read out of the two `routes.json` files, which are authoritative. Three things in
them are worth knowing:

- **`payment` has no gateway route.** It is reached provider-side by webhook and internally by its
  siblings; nothing customer-facing proxies to it.
- **`config`, `scheduler` and `eventbridge` carry no customer prefix on purpose** — cron fan-out,
  the event bus and runtime configuration are consumed service-to-service, never from a customer
  request. `eventbridge` is exposed on the operator side only, for inbound webhooks.
- **Port 8170 is claimed twice.** `app-gateway` proxies `/api/v1/projects-workspace` to 8170, and
  `admin-gateway` proxies all five `/api/v1/admin/*` prefixes to 8170 as well. `projects` and
  `console` cannot both hold it on one host. This is a live defect, not a documented convention.

Five further prefixes route to services that do not exist yet — `/leave` (8122), `/payroll`
(8123), `/recruit` (8124), `/campaigns` (8132) and `/tickets` (8133). They are either reserved
intent or dead entries; either way a request to one reaches nothing.

**Two gateways rather than one**, because the alternative puts a surface that grants cross-customer
access one routing mistake away from the public internet. They also scale on different terms: the
customer surface is chatty and latency-sensitive, the operator surface is broad and low-volume.

Routing is **data, not code**. Each gateway owns a `routes.json` mapping a URL prefix to an
upstream and to the product whose entitlement gates it. Longest prefix wins. A service that moves
is one edit there rather than a hunt through code.

---

## 5. The request path

The order of checks at the gateway is cheapest-and-most-conclusive first, which is also the order
that leaks the least:

| # | Check | Failure |
|---|---|---|
| 1 | Route exists | `404` — before authenticating |
| 2 | Token verifies: signature, issuer, **audience**, expiry, type | `401` |
| 3 | Token carries an organization | `401` |
| 4 | Permission version current, checked against Redis | `401 token-stale` |
| 5 | Product entitled | `402` — not `403` |
| 6 | Rate limit | `429` |
| 7 | Proxy to the upstream | |

Rate limiting sits **after** authentication so it counts against a person rather than an address,
which is what makes a shared office NAT usable.

**Inbound `X-*` identity headers are stripped** before the gateway writes its own. Without that, a
caller claims to be somebody else and the internal token the gateway adds vouches for it.

`402` rather than `403` for an unentitled product is deliberate: it distinguishes *you have not
bought this* from *you are not allowed this*, which are different conversations with the customer.

---

## 6. Tenancy and isolation

**Shared schema, `organization_id` on every tenant row, filtered in every query.** Not a database
per tenant, not a schema per tenant — those move isolation into connection routing and make
cross-tenant reporting and migrations painful at this scale.

Underneath sits **row-level security**, and it is implemented (ADR-0002).

Three mechanisms, in order of how much weight they carry:

**1. The query filter.** Every read lives in `app/selectors.py`, filters `organization_id ==
ctx.organization_id` explicitly, and calls `ctx.apply(...)` to narrow by scope.

**2. The tenant GUC, bound per transaction.**

```sql
SELECT set_config('app.organization_id', :org, true)
```

The trailing `true` makes it **transaction-local**. Setting it per connection is a cross-tenant
leak, because the pool reuses connections across requests. This is bound by an `after_begin`
listener so no route can forget it.

**3. Per-service Postgres roles.** One shared application role would let `account` run
`SELECT * FROM auth.credentials`. Instead every service connects as its own `NOBYPASSRLS` login
role — `opsgrids_account`, `opsgrids_auth`, `opsgrids_lead` … — granted usage on its own schema and
nothing else. A missing tenant filter therefore returns *nothing*, not *everything*.

Four roles deliberately hold `BYPASSRLS`, each confined to named tables:

| Role | May read | Why it must bypass |
|---|---|---|
| `opsgrids_admin` | everything | Runs migrations and backfills, which legitimately span organizations |
| `opsgrids_directory` | a handful of `account` tables, SELECT only | Sign-in resolves a person *before* an organization is known |
| `opsgrids_product_directory` | `product.activations`, `product.products`, SELECT only | auth-service resolves entitlements while minting the token that would establish tenancy |
| `opsgrids_console_directory` | console's projection tables, SELECT only | The operator console reads across every tenant by definition |
| `opsgrids_outbox` | `outbox`, SELECT and UPDATE only | The relay's query exists to *discover* which organizations have work, so it can bind no tenant |

Getting that last one wrong is the failure mode with no error message: as a `NOBYPASSRLS` role the
relay's drain query matches zero rows, so it publishes nothing, logs nothing, and loops while the
outbox fills.

---

## 7. Identity

The decision that shapes everything else: **a user is global, a membership is per-organization.**

```
users  (email unique platform-wide, citext)
   └── memberships  (user × organization)
          ├── roles → role_permissions → permissions
          ├── locations, org unit, manager
          └── employee reference
```

One person works for two customers with one login. The **membership**, not the user, is what a
product means by "a user": it carries roles, locations and the employee link.

Organization is resolved at sign-in from a slug hint, the host, or a single membership. **Never
from a constant, and never from the email domain** — people use personal addresses and domains are
shared.

### Tokens

- **EdDSA (Ed25519), verified through JWKS.** Only `auth-service` holds a private key; everything
  else fetches the public document.
- **Per audience.** An `app` token is refused by `admin-gateway`, and vice versa.
- **No permission list in the token.** Permissions are resolved at the gateway per request. A fat
  token is 5–6 KB of header across five products, past common proxy limits, failing as a silent
  500.
- **A `pv` claim** carries the permission version. The gateway compares it against Redis and
  refuses anything older, so a revoked grant dies on the next request rather than at expiry. This
  is why Redis is a hard dependency of any write that changes permissions — the counter bump
  raises if it cannot happen, because telling an administrator the revocation failed beats
  reporting success while the person keeps access.

The `Principal` the platform passes around carries: `sub`, `organization_id`, `membership_id`,
`subject_ref` (product-specific — an employee id for HR, a rep id for CRM), `audience`,
`session_id`, `entitlements`, `roles`, `perms`, `locations`, `amr` (drives step-up) and
`permission_version`.

### The auth surface

```
/auth/login            /auth/refresh          /auth/logout        /auth/me
/auth/select-org       /auth/switch-org
/auth/password/{change,forgot,reset}
/auth/mfa/{enrol,enrol/confirm,verify}
/auth/sso/{config,start,callback}
/auth/operator/{login,refresh,logout}
```

**OIDC single sign-on is implemented** per organization. **SAML is not** — `org_idp_configs`
carries the SAML columns it always did, with nothing reading them. That is deferred deliberately
rather than half-built: SAML is a different trust model, with signed XML assertions, XML
canonicalisation, signature-wrapping attacks and IdP-initiated flows that have no request to bind
a response to.

### Service-to-service

The gateway and sibling services prove a call's origin with a signed internal token, bound to a
caller and an audience, with a 60-second window. Today that signature is **one shared HMAC
secret** — a service that can verify can also mint. Per-service keypairs or mTLS is the intended
end state and is on the backlog.

---

## 8. Authorization

Grammar: **`product.module.resource.action:SCOPE`** — for example
`pulseboard.standup.entry.read:ORG`.

Actions are data, so a new verb is a row rather than a migration.

Scope ladder: **`OWN < TEAM < LOCATION < ORG`**. Where two roles grant the same permission at
different scopes, **the widest wins** — taking the last row seen would make a person's permissions
depend on row order.

The ladder is universal but **what each rung means is per product**. TEAM and LOCATION are
HR-shaped ideas; CRM's natural narrowing is territory or account ownership. Each service therefore
registers a `ScopeResolver` declaring which column is the owner, the team and the location. A rung
a product does not offer is **refused rather than silently widened**.

Each product service declares its vocabulary in `app/catalogue.py`: `PermissionSpec` rows and
`RoleTemplate` rows. Two rules are enforced at import and asserted by a test:

- **A product's admin role is built from that product's own catalogue**, never "all permissions".
- A template that grants outside its own product raises, unless `cross_product=True` is set
  deliberately.

`tools/export_permissions.py` merges every service's declarations into one manifest per product
under `libs/contracts/permissions/`. Two services may contribute to one product — `pulseboard` is
assembled from `project`, `standup` and `timesheet` — and the merge hard-fails if they disagree
about a shared role's name or default flag.

**Authorization is server-side.** A UI gate hides a control; it does not protect data. Every list
endpoint injects scope into the query, and every endpoint carries a negative-authz test.

---

## 9. Service anatomy

Every service is the same shape. Learn one, navigate all of them.

| Module | Holds |
|---|---|
| `app/main.py` | The `create_app()` call. No logic. |
| `app/config.py` | `Settings(ServiceSettings)` — fixes `service_name`, `db_schema`, the roles |
| `app/models.py` | SQLAlchemy tables on a schema-scoped `Base` |
| `app/schemas.py` | Pydantic contracts — this is the OpenAPI surface |
| `app/api/` | Routers. Each route declares its permission |
| `app/selectors.py` | **Reads.** Every scoped query, calling `ctx.apply()` |
| `app/services.py` | **Writes.** Rules, transactions, audit rows, outbox rows |
| `app/domain.py` | Pure functions, no I/O. Only where real algorithms exist |
| `app/scope.py` | This product's `ScopeResolver` |
| `app/catalogue.py` | This service's permissions and role templates |
| `app/consumers.py` | Event handlers. Idempotent. Own process |
| `app/relay.py` | Outbox relay. Own process |

The layering is machine-enforced by `.importlinter`:

```
main → (api | consumers | relay | cron) → services → selectors → (schemas | config) → domain → models
```

Nothing imports upward, and no cycles. `make boundaries` fails the build otherwise.

**Three entrypoints** per service: `bin/api` (uvicorn), `bin/worker` (consumers), `bin/cron`
(scheduled sweeps). Each runs as its own process, so a slow broker cannot block an API request.

Every service also carries `OutboxEvent`, `ProcessedEvent` and `AuditLog` on its own schema.

---

## 10. Service inventory

**22 services are built out. 9 are generated skeletons** — `app/services.py` is a docstring,
`app/selectors.py` is seven lines, and the only tables are the three every service gets.

| Area | Built | Skeleton |
|---|---|---|
| Identity & org | `auth`, `account`, `product` | `config` |
| People (HRMS) | `employee`, `attendance` | — |
| Revenue (CRM) | `customer`, `deal`, `marketing`, `support` | `lead`, `pipeline`, `activity` |
| Work (PulseBoard) | `project`, `timesheet`, `standup`, `projects` | `quotation` |
| Money (commerce) | `catalog`, `subscription`, `metering`, `billing`, `payment`, `console` | — |
| Finance, ops, ITSM | `finance`, `operations`, `itsm` | — |
| Platform | — | `document`, `notification`, `scheduler`, `eventbridge` |
| Edge | `app-gateway`, `admin-gateway` | — |

By size, the five largest: **`finance`** (20 models, 29 routes, ~8,400 test lines), **`account`**
(14 models, 25 routes), **`auth`** (13 models, 20 routes), **`subscription`**, **`attendance`**.

The three CRM skeletons matter: the gateway already routes `/api/v1/leads`, `/api/v1/pipelines`
and `/api/v1/activities` under `"product": "salescrm"`, but none of the three has a catalogue, so
**no `salescrm.json` manifest exists** and that product's entitlement gate has nothing behind it.

---

## 11. Events

Writes stage a row into an **outbox** in the same transaction as the state change and the audit
row. A **relay** process drains it with `FOR UPDATE SKIP LOCKED` and publishes to RabbitMQ.
Consumers are idempotent through a `processed_events` ledger.

Background work is enqueued **only after commit**, via the outbox — never inline.

The wire envelope, additive-only within a major version:

```json
{
  "event_id": "…",         "event_type": "pulseboard.standup.entry.posted",
  "version": 1,            "organization_id": "…",
  "aggregate_type": "…",   "aggregate_id": "…",
  "occurred_at": "…",      "producer": "standup",
  "correlation_id": "…",   "causation_id": "…",
  "actor": {…},            "schema_uri": "opsgrids://contracts/<type>/v<n>",
  "payload": {…}
}
```

**Current state of the consuming half:** every service ships a worker process and a `consumers.py`,
but only **six services register handlers** — `console` (4), `account` (2), `payment` (2),
`subscription` (2), `product` (1), `finance` (1). The other 25 start, connect, and drop every
message. Failures retry once and park in `{queue}.dlq`; `tools/dlq.py` inspects and replays them,
and nothing alerts on depth yet.

---

## 12. Data

**One schema per service. No cross-schema foreign keys.** This is what keeps a later
database-per-service split a configuration change rather than a rewrite. A reference to another
service's row is a plain indexed UUID, never an FK.

`TenantBase` gives every tenant table the same five columns — `id`, `organization_id`,
`created_at`, `updated_at`, `deleted_at` — so a service declares only its business columns. Every
index leads with `organization_id`, because the policy qual is ANDed into every query.

**Money is integer minor units with an ISO currency.** No floats, no `Numeric(_, 2)`. Rates are
carried in micros, and allocation uses largest-remainder so split lines always sum back to the
total. This was settled before any billing code existed, because it is unfixable once invoices do.

**Append-only, enforced below the application:** invoices, usage events, the credit ledger,
refunds, provider events, and console's revenue projection. `UPDATE` and `DELETE` are revoked from
the owning role in a service migration, and a finalize-freeze trigger survives the post-migration
grant re-apply. A correction is a new reversing row; balances are derived, never stored.

**Plan versions are immutable** and subscriptions pin to a version.

**PII is encrypted and masked by default.** Unmasking needs its own permission and writes an audit
row every time, through `record_sensitive_read`.

---

## 13. Commerce

Five services, split by what each owns and how fast that thing changes (ADR-0004):

| Service | Owns | Why its own boundary |
|---|---|---|
| `catalog` | Products, plans, plan versions, features, prices | The definitional layer — *what can be sold*. Immutable by version. |
| `subscription` | Onboarding → trial → active → renewal / upgrade / downgrade → cancelled | The state machine — *what this customer has*. Entitlement is decided here. |
| `metering` | Usage events, append-only | *How much was used.* Different scaling shape, and the one thing you cannot backfill. |
| `billing` | Invoices (frozen at finalize), proration, the credit ledger | *What is owed.* Must be reconstructable years later. |
| `payment` | Provider adapters, mandates, charges, reconciliation | *What was collected*, and the provider-specific mess behind it. |

The seam that matters most: **`catalog` immutable, `subscription` mutable.** Catalog holds frozen
plan versions; subscription holds a mutable pointer to one. A customer's life changes constantly;
what they are pinned to does not change under them.

Custom pricing is a bespoke plan version, pinned — not an edited plan. Grants, discounts, credits
and comp access are **ledger entries, never mutable balances**. Usage is metered now and billed
later, because turning on usage pricing afterwards is then plan configuration rather than an
architecture change.

`console` is the operator read model, projecting cross-tenant commercial state for staff.

## 14. Finance and GST

The largest service in the platform. It owns invoices and their tax heads, e-invoice filing to the
**IRP**, the **IMS** supplier-reconciliation queue, **e-Way bills**, **GSTR-1**, and **GST-TDS**
withholding.

Three portals are stubbed at exactly one seam — the outbound HTTP. The state machines, retry
policy, idempotency guards, validity arithmetic and persistence are real and exercised;
`IrpClient`, `ImsClient` and `EwayClient` are the whole change surface when credentials arrive.

Deliberately out of scope, so it is not re-litigated: nothing files a return by itself, no route
lets a person type a withholding in, an e-Way bill is never raised automatically, and nothing
auto-accepts an IMS document.

---

## 15. Frontend

One standalone app per surface (ADR-0001). A customer who bought only CRM downloads only CRM.

```
opsgrids-internal    operator console (staff, cross-tenant) Vite · React
product-*          nine product apps                      Vite · React
opsgrids-ui          the design system every app imports
opsgrids-client      auth session, gateway HTTP, problem+json parsing
opsgrids-config      shared tsconfig / eslint / vite presets
```

**An app may not fork a component into itself.** A product variant becomes a prop on the shared
component. Nine apps growing nine date pickers is the failure that rule prevents.

The design language is **"Console"**: Manrope, a single violet accent spent sparingly, a light-grey
ground with white rounded cards, a grouped left sidebar, and a full dark theme. Semantic colour is
separate from the accent and never doubles as decoration.

**Data and auth in the client:** the access token lives **in memory only** — never
`localStorage`, which any XSS payload can read. The refresh token is an httpOnly cookie the
JavaScript never sees. Errors arrive as **RFC 7807 `problem+json`** and are parsed into typed
errors, with field-level detail mapped onto the control that caused it. A 4xx is never retried.

**Current wiring:** six product apps default to live data; `product-crm`, `product-pulseboard` and
`opsgrids-internal` still default to fixtures. Several live apps carry named sample islands — an
Overview trend chart, a sparkline — flagged in the UI with a "Sample data" chip rather than passed
off as real. `opsgrids-internal` is a deliberate fork off the shared packages, because its palette is
a different token family and its auth is the staff registry, which has no organization selection.

---

## 16. Contracts

Two generated, committed artefacts under `libs/contracts/`:

- **`openapi/*.json`** — 33 documents, one per service, produced by `tools/export_openapi.py`
- **`permissions/*.json`** — 10 product manifests, produced by `tools/export_permissions.py`

Both are regenerated with `make openapi` and **never hand-edited**. `make openapi-check` fails CI
when a committed document is behind the code. Both generators discover services rather than listing
them, so a new service needs no registry edit.

---

## 17. Quality gate

`make gate` is what CI runs, and every row names the thing that failed:

| Row | Tool |
|---|---|
| Lint, format | **ruff** — E, W, F, I, UP, B, C4, SIM, ASYNC, S (bandit), T10, T20, PGH, RUF |
| Types | **mypy strict**, pydantic plugin, run **per service** |
| Tests | **pytest** + asyncio, cov, xdist; **hypothesis** for money allocation and rounding |
| Layering | **import-linter** — the anatomy above, and no cycles |
| Vulnerabilities | **pip-audit** over what `uv.lock` resolves to |
| Licences | **pip-licenses** against an allow-list |
| Secrets | **detect-secrets** against an audited baseline |
| Hygiene | no skipped tests, no `print()`, no debugger, no bare `noqa` |
| Contracts | OpenAPI and permission manifests match the code |
| Migrations | `alembic check`, plus upgrade → downgrade → upgrade on every service |
| Boot smoke | the real uvicorn entrypoint, polled on `/health` and `/health/ready` |
| Frontend | `pnpm -r typecheck / lint / test / build` |

**Tests run against PostgreSQL, never SQLite** — the domain leans on PostGIS, `SKIP LOCKED`,
`ON CONFLICT` and row-level security, none of which SQLite has.

`e2e/` is a separate suite where nothing is stubbed: it boots real services, a relay and a
consumer on OS-allocated ports and drives one ordered journey over real HTTP.

---

## 18. Deployment shape

One image per service. Multi-stage: dependencies resolve once at the workspace root from
`uv.lock`, then exactly one service's code is copied in.

```
ghcr.io/astral-sh/uv:python3.12-bookworm-slim   →   python:3.12-slim-bookworm
```

Non-root user, `HEALTHCHECK` on `/health`, `CMD` runs uvicorn. Dev, staging and production differ
only in configuration — same engine versions everywhere, because dev/prod parity is not optional
when the domain leans on PostGIS and `SKIP LOCKED`.

Configuration comes from the environment; the workspace `.env` is found by walking up for the root
`pyproject.toml`, so a process started inside `services/<name>` still reads it. Production refuses
to boot on a `dev-only-` or `ci-only-` placeholder secret.

---

## 19. What is not built

Stated plainly, because an architecture document that only describes the good parts is a sales
deck.

| Gap | State |
|---|---|
| **Deployment** | No Kubernetes manifests, no CD, no environment. `infrastructure/` is an empty directory. |
| **Backups** | Unconfigured. Mandatory before any data migration, and needs to be *rehearsed*, not written. |
| **Observability** | No metrics, no tracing, no error tracking. Correlation ids are propagated and nothing collects them. |
| **Event handlers** | 25 of 31 services consume nothing (§11). |
| **Internal auth** | One shared HMAC secret, not per-service keypairs (§7). |
| **Alerting** | `/health/outbox` exposes relay lag and `tools/dlq.py` inspects dead letters; nothing scrapes or alerts on either. |
| **Rate limiting** | Per-caller counters only. No global or per-IP ceiling on the unauthenticated surface. |
| **Secrets** | From `.env`. No secret manager, no rotation story. |
| **SAML, JIT provisioning** | Columns exist and are inert, by decision (§7). |
| **Nine skeleton services** | Generated, unfilled (§10). |
| **Frontend test coverage** | Near zero — one test file across 14 workspace projects. |

The single item on the shortest path to a pilot deployment is the generated per-route cross-tenant
sweep: it exists for `account` only, and every other service has hand-written isolation tests
instead.

---

## Related documents

| Document | For |
|---|---|
| [`adr/0000-phase-0-decisions.md`](adr/0000-phase-0-decisions.md) | The eleven decisions that are expensive or impossible to reverse |
| [`adr/0001-frontend-topology.md`](adr/0001-frontend-topology.md) | Why one app per surface |
| [`adr/0002-row-level-security.md`](adr/0002-row-level-security.md) | The tenancy net (§6) |
| [`adr/0003-surfaces-and-authorization-topology.md`](adr/0003-surfaces-and-authorization-topology.md) | Who sees what, and where that is decided |
| [`adr/0004-commercial-model.md`](adr/0004-commercial-model.md) | Commerce (§13) |
| [`adr/0005-dunning-and-grace.md`](adr/0005-dunning-and-grace.md) | What happens when payment fails |
| [`api-reference.md`](api-reference.md) | Calling the API |
| [`testing.md`](testing.md) | Writing and running tests |
| [`backlog.md`](backlog.md) | What is next and what is blocked |
| [`production-readiness.md`](production-readiness.md) | Whether this can be deployed |
