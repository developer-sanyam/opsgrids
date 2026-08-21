# Architecture

The narrative version. For the visual one-pager see [architecture.html](architecture.html); for
why each decision was taken, [decisions.md](decisions.md).

---

## The shape

```
CLIENTS      product SPAs · admin console · operator console · mobile · partners
                                    │
EDGE         ┌──────────────────────┴──────────────────────┐
             │  app-gateway (:8100)     admin-gateway (:8101) │
             │  product surface         operator + internal   │
             └──────────────────────┬──────────────────────┘
                                    │  verify once, resolve tenancy,
                                    │  gate on entitlement, proxy
SERVICES     auth (:8110) · account (:8111) · [product services — not built]

DATA         PostgreSQL (schema per service, org_id on every row)
             Redis · RabbitMQ · MinIO
```

Two gateways rather than one because the alternative puts a surface that grants cross-customer
access one routing mistake away from the public internet. They also scale and rate-limit on
different terms: the product surface is chatty and latency-sensitive, the operator surface is
broad and low-volume.

## Tenancy

**Shared schema, `organization_id` on every tenant row, filtered in every query.** Not a database
per tenant, not a schema per tenant — those move isolation into connection routing and make
cross-tenant reporting and migrations painful at the scale this is aimed at.

Row-level security is meant to sit underneath as the net. **It is not implemented.** See
[ADR-0002](adr/0002-row-level-security.md) and [production-readiness.md](production-readiness.md).
Today, isolation is exactly as good as the discipline of the last query somebody wrote.

## Identity

The decision that shapes everything else: **a user is global, a membership is per-organization.**

```
users (email unique platform-wide)
   └── memberships (user × organization)
          ├── roles → role_permissions → permissions
          ├── locations, org unit, manager
          └── employee reference
```

One person can work for two customers with one login. The membership — not the user — is what a
product means by "a user": it carries roles, locations and the employee link.

Organization is resolved at sign-in from a slug hint, the host, or a single membership. **Never
from a constant, and never from the email domain** — people use personal addresses, and domains
are shared.

## Tokens

- **EdDSA (Ed25519), verified through JWKS.** Only auth-service holds a private key.
- **Per audience.** An `app` token is refused by `admin-gateway` and vice versa.
- **No permission list.** Permissions are resolved at the gateway per request. A fat token is
  5–6 KB of header across five products — past common proxy limits, failing as a silent 500.
- **A `pv` claim** carries the permission version. The gateway compares it against Redis and
  refuses anything older, so a revoked grant dies on the next request rather than at expiry.

## Authorization

Grammar: `product.module.resource.action:SCOPE`, with scopes `OWN < TEAM < LOCATION < ORG`.
Actions are data, so a new verb is a row rather than a migration. Where two roles grant the same
permission at different scopes, **the widest wins** — taking the last row seen would make a
person's permissions depend on row order.

## Requests

Order of checks at the gateway, cheapest and most conclusive first — which is also the order
that leaks the least:

1. Route exists → `404` before bothering to authenticate
2. Token verifies: signature, issuer, **audience**, expiry, type
3. Token carries an organization
4. Permission version current → else `401 token-stale`
5. Product entitled → else `402`, not `403`
6. Rate limit → `429`
7. Proxy

Rate limiting sits *after* authentication so it counts against a person rather than an address,
which is what makes a shared office NAT usable.

**Inbound `X-*` identity headers are stripped** before the gateway writes its own. Without that,
a caller claims to be somebody else and the internal token the gateway adds vouches for it.

## Services

Each owns a schema, exposes REST, publishes through a transactional outbox, and consumes
idempotently. **No service reads another's tables.** auth-service needs directory data and asks
account-service over the internal API rather than joining across a schema boundary — which is
what keeps a later database-per-service split a configuration change.

## Events

Writes stage into an outbox in the same transaction as the state change and the audit row. A
relay drains it with `FOR UPDATE SKIP LOCKED` and publishes to RabbitMQ. Consumers are idempotent
through a `processed_events` ledger.

**Nothing consumes anything yet** — zero handlers are registered. The producing half is built and
tested; the reacting half is not.

## Money

Integer minor units with an ISO currency, rates in micros, largest-remainder allocation so split
lines always sum back to the total. Settled before any billing code exists because it is
unfixable once invoices do.

## Frontend

One standalone app per surface ([ADR-0001](adr/0001-frontend-topology.md)): Next.js where SEO and
static rendering matter, Vite + React for signed-in apps. Only the marketing site exists; the
shared `atmora-ui` and `atmora-client` packages are declared and empty.
