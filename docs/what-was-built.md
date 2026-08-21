# What was built

An inventory of what exists, and how far each piece is trusted. Counts were taken from the
code, not from memory; where a number is here, it was measured.

**Totals: 5 deployable units, 194 passing tests, 20 declared tables across 2 schemas.**

---

## Shared library — `libs/platform-core`

The thing every service imports. 44 tests.

| Module | What it gives you |
|---|---|
| `app.py` | App factory — lifespan, health routes, problem-detail handlers, request logging |
| `auth.py` | `Principal`, `ScopeContext`, `require(...)`, scope ladder, JWKS verification |
| `db.py` | Engine, sessions, `set_tenant`, `get_scoped` |
| `events.py` | Transactional outbox, relay, idempotent consumer base |
| `cache.py` | Redis; rate limiting and permission versions, both fail-closed |
| `gateway.py` | The entire gateway: routing, token checks, entitlement, proxying |
| `audit.py` | Audit records with deep redaction |
| `money.py` | Integer minor units, largest-remainder allocation |
| `errors.py`, `config.py`, `logging.py`, `models.py`, `pagination.py` | The rest of the floor |

**Built**, with one caveat worth knowing: `db.set_tenant` and `db.session_scope` are written,
tested in isolation, and **called by nothing**. They are the application half of row-level
security, waiting on [ADR-0002](adr/0002-row-level-security.md).

## `account-service` — the organizational skeleton

**11 declared tables** (plus outbox, processed-events and audit tables from the shared mixins),
**21 public routes**, **3 internal routes**, **63 tests**.

Owns organizations, org units, locations, **global users**, memberships, roles, permissions and
invitations. The identity model is the important part: a **user is global** — email is unique
platform-wide — and a **membership** is what ties one person to one organization, carrying their
roles, locations and employee reference. That is what lets one person work for two customers
without holding two accounts.

Also serves the **internal API** (`/internal/v1`), mounted at the root and excluded from the
public schema. Three endpoints, and they are the only ones that read across tenants by design:
auth resolves sign-in through two of them, and **both gateways call the third on every
authenticated request**. If this router is down, nobody can sign in and nothing is authorized.

## `auth-service` — credentials and tokens

**9 declared tables**, **13 routes**, **41 tests**.

The only service in the platform that holds a **private signing key**; everything else verifies
through JWKS. Owns credentials (Argon2id), sessions, refresh-token families with rotation and
reuse detection, TOTP MFA with recovery codes, password reset, external identities and per-org
SSO configuration.

It deliberately owns **no directory data**. It asks account-service over the internal API rather
than reading across a schema boundary — which is what keeps a later database-per-service split a
configuration change rather than a rewrite.

## `app-gateway` and `admin-gateway`

**31 + 15 tests. No tables, no database.**

Both are `platform_core.gateway` plus a `routes.json`. Everything they need is in the token or
in Redis. Checks run cheapest-first: route exists → token verifies against **this gateway's
audience** → permission version current → product entitled → rate limit → proxy.

Two behaviours worth internalising before you touch either:

- **Inbound `X-*` identity headers are stripped.** Without that, a caller could simply claim to
  be someone else and the internal token the gateway adds would vouch for it.
- **402 is not 403.** A product the organization has not activated returns *payment required* —
  the caller's permissions are fine, the subscription is not, so the client can offer an upgrade
  instead of an error.

`admin-gateway` differs in three ways: a distinct `operator` audience, so a stolen product token
is inert on it; per-route `staff_only`, which answers **404 rather than 403** so a prober cannot
confirm the route exists; and no entitlement gate, because staff must be able to act on
organizations that have bought nothing.

## Frontend

| Package | State |
|---|---|
| `atmora-website` | **Built** — Next.js 16 marketing site, 9-product catalogue, runs on `:4300` |
| `atmora-config` | **Built** — shared strict TypeScript config |
| `atmora-ui` | **Absent** — declared workspace package, `src/` is empty |
| `atmora-client` | **Absent** — declared workspace package, `src/` is empty |

There is **no product UI**. No dashboard, no signed-in shell, nothing that consumes the API.

## Tooling

`make new-service` scaffolds a working service — app, config, models, migrations, tests,
Dockerfile, entrypoints — in one command. `tools/dev-bootstrap` brings up the whole stack.
CI runs lint, typecheck and per-service tests against real PostgreSQL. Tests never run against
SQLite: the schema leans on `citext`, partial unique indexes and JSONB, and a test that passes
on SQLite proves nothing about the database this ships on.

---

## Test counts

| Unit | Tests |
|---|---|
| `libs/platform-core` | 44 |
| `account-service` | 63 |
| `auth-service` | 41 |
| `app-gateway` | 31 |
| `admin-gateway` | 15 |
| **Total** | **194** |

## What these tests do not cover

Worth stating plainly, because the number above looks reassuring on its own:

- **No end-to-end test exists.** Nothing runs the services together. Each side's suite passes by
  stubbing the other — which is exactly how two real defects survived: account-service served
  none of the internal endpoints auth and the gateways depend on, and the permission-version
  counter was never incremented, so revocation silently did nothing. Both are fixed; neither was
  caught by a unit test, and the suite that would have caught them is still unwritten.
- **No cross-tenant isolation suite.** The generated "enumerate every route, call it with
  another org's token, assert it is never 200" gate is the acceptance test for tenancy, and it
  is not written. Until row-level security lands it could only test the application filter
  anyway.
- **No frontend tests, no visual regression, no accessibility checks** — there is no product UI
  to test.
