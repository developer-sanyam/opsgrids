# Features

What the platform can do today, what half-works, and what is only a decision on paper.

Legend: **Built** — works and is tested · **Partial** — exists, not wired end to end ·
**Absent** — planned or decided, not written.

---

## Identity and access

| Capability | State | Notes |
|---|---|---|
| Create an organization | Built | Also seeds the permission catalogue and default roles |
| Departments, locations | Built | Department tree is stored as a hierarchy and read as one |
| Global user, per-org membership | Built | One person, many organizations, one login |
| Invite and accept | Built | Accepting is a **public** route — the token is the credential |
| Roles and permissions | Built | Grammar is `product.module.resource.action:SCOPE` |
| Scope ladder `OWN < TEAM < LOCATION < ORG` | Built | Widest scope wins when two roles overlap |
| Sign in, sign out | Built | |
| Choose and switch organization | Built | New token, same session |
| Refresh-token rotation with reuse detection | Built | Reusing a rotated token kills the family |
| TOTP MFA and recovery codes | Built | Step-replay protected |
| Password reset | Built | |
| Per-org SSO configuration | Partial | Config is modelled; **no OIDC or SAML flow is implemented** |
| Revocation takes effect immediately | Built | Permission version bumps on all four grant-changing paths |
| Staff / operator surface | Partial | The gateway enforces it; `admin-service` behind it does not exist |

## Tenancy and isolation

| Capability | State | Notes |
|---|---|---|
| `organization_id` on every tenant row | Built | |
| Every query filtered by organization | Built | Via selectors, by convention |
| **Row-level security** | **Absent** | [ADR-0002](adr/0002-row-level-security.md) — proposed, not accepted |
| Non-superuser application role | **Absent** | The app connects as a **superuser** today |
| Cross-tenant isolation test suite | Absent | The acceptance gate for tenancy |

**Read this row carefully.** Isolation currently rests *entirely* on every query carrying the
right filter. The net that is supposed to sit under that — RLS — is designed and not built, and
three separate things must change together before it does anything at all. Until then, a
forgotten `WHERE organization_id = …` is a cross-tenant data leak with nothing to stop it.

## Gateways

| Capability | State |
|---|---|
| Route table as data, per gateway | Built |
| Token verification per audience | Built |
| Identity-header stripping | Built |
| Permission resolution, cached against the version | Built |
| Product entitlement gate (402) | Partial — reads token claims; no registry behind it |
| Rate limiting, fail-closed | Built |
| Staff-only routes answering 404 | Built |

## Products

**None of them exist.** HRMS, CRM, PulseBoard, Sales CRM, Project Management, IT Management,
Marketing, Finance and Operations are named in the marketing site's catalogue and in the plan.
No product service has been written. The platform can currently create an organization, invite
people, sign them in, and authorize them against products that are not there yet.

## Billing

| Capability | State |
|---|---|
| Plans, subscriptions, metering, invoices | Absent |
| Razorpay / Stripe adapters | Absent |
| Money primitives — minor units, allocation | **Built** in `platform_core.money` |

The money representation was settled early because it is unfixable after invoices exist:
integers in minor units with an ISO currency, rates in micros, largest-remainder allocation so
split lines always sum back to the total.

## Platform services

`document`, `notification`, `audit` (as a service), `scheduler`, `eventbridge`,
`product-service`, `config-service` — **all absent**. The shared library provides audit
recording and the outbox; the services that would centralise them do not exist.

## Events

| Capability | State |
|---|---|
| Transactional outbox | Built |
| Relay draining with `FOR UPDATE SKIP LOCKED` | Built |
| Idempotent consumer base, `processed_events` ledger | Built |
| Events actually published by account-service | Built |
| **Any consumer handling any event** | **Absent** — zero handlers registered |

So events are written and drained, and nothing reacts to them. The traced flows in the plan —
an employee joining causing five services to do work — describe machinery that is built at the
producing end and empty at the consuming end.

## Observability

| Capability | State |
|---|---|
| Structured logging, correlation IDs | Built |
| Health and readiness endpoints | Built |
| Outbox-lag alerting | **Absent** — and worth having *before* RLS lands |
| Metrics, tracing, error tracking | Absent |

A stalled relay is currently invisible: it logs once at startup, then sleeps in a loop finding
nothing, and the process looks healthy while the outbox grows without bound.
