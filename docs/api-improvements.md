# API improvements

Concrete, specific changes worth making to the API surface. Each is something found while
reading or testing the code, not a general aspiration. Ordered by how much it matters.

---

## 1. The internal service token is a shared static secret

**Severity: high.** `require_service_token` compares an `X-Internal-Token` header against a
configured string that defaults to `dev-only-internal-token`. One leaked value reads **every
user, every membership and every organization name on the platform**, because that is exactly
what the internal API is for.

The router is excluded from the OpenAPI document, which is obscurity, not a control.

**Fix:** a short-TTL signed service token with an audience claim, or mTLS. Plus a network policy
making `/internal/v1` unreachable from ingress. Until then, treat the token as a production
secret and never let the default ship.

## 2. Cross-tenant reads left no trace — **partly fixed**

**Severity: high.** The internal endpoints are the highest-value reads in the system — one
returns, for a single email, the id, slug and name of every organization that person belongs to
— and none left any record.

**The original recommendation here was to call `record_sensitive_read` from all three. That was
wrong**, for three reasons that only surface on contact with the code:

- It writes an `audit_logs` row, and `organization_id` is NOT NULL. These lookups resolve
  somebody **before an organization is known** — that is their whole purpose — so there is no
  value to put in the column.
- The caller is a **service**, so there is no actor to attribute the row to either.
- They are `GET`s on a session that never commits, so a staged row is simply discarded.

And the grants endpoint is called by both gateways on **every authenticated request**. A row per
call would put a write on the hottest path in the platform and bury the signal in volume.

**Done instead:** a structured log line on the two user lookups, which happen at sign-in
frequency — recording the resolved user id, how many organizations came back, and the calling
service. The email is deliberately not logged; putting the identifier being looked up into every
log aggregator duplicates the disclosure rather than recording it. The grants endpoint is left
untraced by design.

**Still open:** `record_sensitive_read` remains uncalled, and is still the right tool for its
actual case — a *user* unmasking PII inside a tenant, where an organization, an actor and a
committing transaction all exist. Nothing unmasks PII yet, so there is nothing to call it from.

## 3. Grants and directory lookups have no cache invalidation signal for the caller

The gateway caches resolved grants for 60s under a key containing the permission version, so a
version bump invalidates by construction. That is right. But **a client** has no way to know its
token went stale until it gets a `401 token-stale` on the next call.

**Fix:** nothing structural — document it (done, in [api-reference.md](api-reference.md)) and
make sure clients treat `token-stale` as *refresh*, not *log out*. Getting this wrong turns a
role change into a surprise logout for the whole organization.

## 4. `/internal/v1/users/by-email` takes the email in a query string

**Severity: low, but real.** Emails are personal data and query strings are the part of a URL
most likely to end up in an access log, a proxy log or an error report.

**Fix:** accept it as a `POST` body, or accept a hash. Low urgency because the endpoint is
internal-only, but the logging exposure is real and the fix is cheap.

## 5. The internal router's docstring is wrong — **fixed**

It stated these were *"the only three endpoints in the platform that read across tenants."*
There is a fourth: `POST /api/v1/invitations/accept` is **public** and it **writes** — creating
a user, a membership and role grants with no organization in context.

Corrected rather than deleted, and it now names all four paths plus the two unfiltered
selectors, because ADR-0002 depends on the full list and someone will reason about tenancy from
this file.

## 6. List endpoints use offset pagination

`{ items, total, page, size }` is fine at current scale and will not be fine for an audit log or
a member list in a large tenant — offset pagination degrades as the offset grows and can skip or
repeat rows when the underlying set changes between pages.

**Fix:** keyset pagination for anything append-heavy, starting with audit. Not urgent; worth
deciding before a product service copies the current shape everywhere.

## 7. No API versioning story beyond the `/v1` prefix

The prefix exists. There is no deprecation policy, no `Sunset` header, and no way for a client
to discover that a field is going away. This is cheap to define now and expensive to retrofit
once external customers integrate.

## 8. Health endpoints do not reflect real readiness

`/health/ready` executes `SELECT 1`. It does not check Redis — which is a **hard dependency** of
any write that changes permissions, and of the gateway's rate limiter and staleness check — and
it does not check outbox lag.

A service whose Redis is gone reports ready and then fails every permission-changing write.

**Fix:** check Redis in readiness. Add an outbox-lag probe, ideally before row-level security
lands so there is a baseline. See [operations.md](operations.md).

## 9. The gateways open a database connection they do not need

`app-gateway`'s config states plainly that it "never opens a database connection". The shared
app factory calls `init_engine` unconditionally, and `/health/ready` runs `SELECT 1` against it,
which readiness probes hit continuously.

Harmless today, but under a database role split it means two more processes needing credentials
and grants for no reason.

**Fix:** make `init_engine` conditional on the service actually owning tables.

## 10. A dead route in the operator route table — **fixed, by deletion**

`admin-gateway`'s `routes.json` mapped `/api/v1/internal/accounts` to account-service, which
mounts its internal router at `/internal/v1`. The prefixes never matched, so the route resolved
at the gateway and 404'd at the upstream.

**Correcting the prefix would have been the wrong fix.** Those three `/api/v1/internal/*` routes
carried no `staff_only` flag, and the internal endpoints have no per-user authorization at all —
the shared service token is the only guard. Making them work would have let any
operator-audience caller, staff or not, read every user and organization on the platform. It was
inert only because of a typo, which is luck rather than design.

Deleted instead, together with the `products` and `billing` equivalents. Nothing used them:
auth-service and both gateways call account-service **directly on its own port**. Two tests now
assert no operator route reaches the internal API, so the property is guarded rather than left
to a prefix mismatch.
