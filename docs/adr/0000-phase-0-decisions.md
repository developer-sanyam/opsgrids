# ADR-0000 — Phase 0 decisions

Status: **accepted** · Date: 2026-08-20

These eleven decisions are cheap now and expensive or impossible to change once real customer
data, issued tokens, or customer-authored roles exist. They are recorded here as one document
because they are interdependent: several of them only make sense together.

Every later ADR may refine these. None may quietly contradict one.

**Clarifying notes added 2026-09-17** (§5, `is_dangerous`). No decision here changed; a note records a
fact about the implementation that later ADRs kept getting wrong, and is placed here because this is
the document they all read.

---

## 1. A user is global; membership is per-organization

`users` is unique on `lower(email)` across the whole platform. Belonging to an organization is a
separate `memberships(user_id, org_id)` row carrying the org-specific facts: roles, employee link,
org unit, locations, status.

**Why now.** The alternative — a user row per organization, unique on `(org_id, email)` — is what
HRMS does today, and it makes "one account across the ecosystem" impossible. Retrofitting it after
two customers have overlapping email addresses is a manual identity-merge project with data-loss
risk. Today it is an index definition.

**Consequence.** Login cannot resolve an organization from credentials alone. It resolves in this
order: explicit org slug in the request → `X-Org-Slug` injected by the gateway from the request
host → exactly one active membership → otherwise return an org picker. Email domain **never**
silently selects an organization; it may only select an identity provider, and only from a
domain that has been DNS-verified.

## 2. Only `auth-service` can mint a token; everyone else verifies via JWKS

Signing is **EdDSA (Ed25519)** with a `kid` header. `auth-service` holds the private key. Every
other service — and every non-Python client — verifies against `GET /.well-known/jwks.json`.
Two keys are active at all times; rotation publishes the new key, waits one full refresh-token
TTL, then switches signing.

**Why now.** A shared symmetric secret means any service that can *verify* a token can also
*forge* one for any user in any organization. That is not a posture a multi-product platform can
adopt later: changing the signing algorithm after tokens are in circulation is a forced global
logout.

## 3. Tokens are per-audience, and carry no permission list

An access token names one product in `aud` and carries only what that product needs. Permissions
are **not** embedded; the gateway resolves them per request from a cache keyed by the `pv` claim.

**Why now.** Five products at roughly forty permissions each is 5–6 KB of `Authorization` header
once base64'd — past common proxy limits, where it is truncated and surfaces as a silent 500 that
is very hard to diagnose. Changing the token shape later invalidates every issued token.

## 4. `pv` — a permission-version claim, checked at the gateway

Every token carries `pv`. Any change to a membership's roles or grants bumps the organization's
permission version in Redis. The gateway compares and refuses stale tokens.

**Why now.** Without it a revoked permission remains usable until the access token expires. A
fifteen-minute window is tolerable for a *grant* and unacceptable for a *revocation* — an employee
who left the company should not keep write access for fifteen minutes. Adding a claim later means
invalidating every token in circulation.

## 5. Permission grammar: `product.module.resource.action:SCOPE`

Four parts plus a scope, e.g. `crm.pipeline.deal.approve:TEAM`. Stored normalised as
`product → module → submodule → action`, with **actions as data rows rather than an enum**, so a
new verb needs no schema migration. Wildcards (`crm.*`) are supported.

**Why now.** Once customers author their own roles, the permission code is customer data. Renaming
it becomes a migration of every customer's saved configuration, and any code we miss silently
revokes or grants access. A three-part code cannot express "everything in CRM", which the
per-product admin role needs.

**Corollary.** A product's admin role is defined against *its own* catalogue, never as "every
permission that exists". The seeder asserts this and fails the build otherwise.

**Note added 2026-09-17, from ADR-0008's implementation review — `is_dangerous` is metadata, not a
control.** A permission row carries `is_dangerous`, and it is easy to read as an enforcement flag.
It is not: `grep -rn is_dangerous libs/platform-core` returns **nothing**, so no authorization gate
consults it. It is declared in a service catalogue, written to `permissions` by
`install_registration`, and exposed on `PermissionOut`. The one place it changes an outcome is
pricing — `catalog/app/domain.py:169` keeps a dangerous permission out of a view-only seat class at
entitlement time. So it is not inert everywhere; it is inert **at request time**. Marking a
permission dangerous makes a write no safer. What constrains a dangerous write is the scope floor on
the route (`minimum=Scope.ORG`), the grant itself, and the audit row. ADR-0008 originally cited the
flag as a mitigation and had to retract it; this note exists so the next ADR does not repeat that.
Two related traps live nearby: `max_scope` is a ladder ceiling and not an allowed-set (ADR-0007 §1,
amended), and `install_registration` skips role codes that already exist (ADR-0007's reason to
exist).

## 6. Scope is a ranked ladder with per-product resolvers

`OWN < TEAM < LOCATION < ORG`. The ladder is universal; how each rung translates into a query
filter is registered per product.

**Why now.** `TEAM` and `LOCATION` are HR concepts. CRM's natural scope is territory or account
ownership; a project tool's is squad membership. Committing every product to one interpretation is
the design decision most likely to be regretted, and the resolver indirection costs nothing today.

## 7. Event envelope v1 carries provenance

Every event: `event_id, event_type, version, org_id, aggregate_type, aggregate_id, occurred_at,
producer, correlation_id, causation_id, actor, schema_uri, payload`.

**Why now.** `correlation_id` and `causation_id` cannot be added retrospectively to consumers
already written against a thinner envelope. Without them, a failed leg in a five-hop provisioning
chain is untraceable — you know something did not happen, but not which hop or on whose behalf.

## 8. Money is integer minor units

`BIGINT` amount plus an ISO-4217 currency code. Unit *rates* that need sub-minor precision are
stored in micros (10⁻⁶). No floats, ever. No `Numeric(_, 2)`.

**Why now.** Two decimal places is wrong for JPY (zero) and KWD (three), and decimal arithmetic
makes exact proration and largest-remainder discount allocation awkward. After invoices exist, the
representation cannot be changed without rewriting financial history.

## 9. Plan versions are immutable; subscriptions pin to a version

Editing a plan clones the active version into a draft, which is edited and published; the previous
version becomes grandfathered. Existing subscriptions stay on their pinned `plan_version_id` until
explicitly migrated by an audited operation that previews the money delta per organization.

**Why now.** Mutating a live plan retroactively re-prices every customer on it and makes past
invoices impossible to reconstruct. An invoice you cannot reconstruct is one you cannot defend in
a dispute.

**Related immutability rules**, adopted for the same reason: invoices are frozen at finalize
(corrections are credit notes), the credit ledger is append-only with balance as `SUM`, usage
events are append-only, and statutory tax rates are versioned by `effective_from` — a re-run for
March must use March's rates.

## 10. Tenancy is a shared schema with `org_id` plus row-level security

Every table carries `organization_id`. Every query filters on it. **And** Postgres RLS policies
enforce it with `FORCE ROW LEVEL SECURITY`, driven by a transaction-local
`set_config('app.organization_id', …, true)`.

No per-tenant database, and no cross-schema foreign keys — `org_id` stays a plain indexed
`NOT NULL` UUID, with a nightly orphan check.

**Why now.** Whether isolation is a query concern or a connection-routing concern determines the
shape of every service. Application-level filtering alone depends on every developer remembering,
forever, on every query; the reference architecture we are following does exactly that and it is
the one place we deliberately go further.

**Operational trap, recorded here because it will bite:** the outbox relay and event consumers
process many organizations in one loop. They must run as a role with `BYPASSRLS`, or set the GUC
per message. Getting this wrong silently halts event processing — nothing errors, the outbox just
fills.

**Second trap:** the tenant GUC must be **transaction-local**. Setting it per connection is a
cross-tenant leak, because the pool hands the same connection to the next request.

## 11. UUIDs are preserved across migration

Where an identifier can be carried over from a system being migrated, it is. PulseBoard
organization id becomes the platform organization id; PulseBoard client id becomes the CRM
customer id.

**Why now.** It is the difference between updating one column and rewriting a foreign key across
ninety-one models — and between a migration that can be verified by checksum and one that needs a
permanent mapping table consulted on every join.

---

## Also settled

- **PII** (bank account, government identifiers) is encrypted at rest with envelope encryption and
  masked by default. Unmasking requires a distinct permission **and writes an audit row every
  time** — a rule an earlier system stated and did not implement, which is why it is called out
  here.
- **Audit** is a shared mixin plus a helper, written inside the same transaction as the change it
  describes. Never a bespoke per-service log table.
- **Timestamps** are `TIMESTAMPTZ` in UTC, converted at the edge using the relevant location's
  timezone — never the browser's.
- **Background work is enqueued only after commit**, via the outbox. Dispatching before the state
  change is durable is the bug the outbox exists to prevent.
