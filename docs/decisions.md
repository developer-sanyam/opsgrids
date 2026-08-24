# Decisions

An index of the architecture decision records, and the short version of each. The ADRs
themselves are the authority — this page exists so you can find the right one quickly.

| ADR | Title | Status |
|---|---|---|
| [0000](adr/0000-phase-0-decisions.md) | Phase 0 — eleven irreversible decisions | Accepted |
| [0001](adr/0001-frontend-topology.md) | One standalone app per surface | Accepted |
| [0002](adr/0002-row-level-security.md) | Row-level security | **Accepted** 2026-08-22, not yet implemented |
| [0003](adr/0003-surfaces-and-authorization-topology.md) | Surfaces and authorization topology | Accepted |
| [0004](adr/0004-commercial-model.md) | Commercial model — plans, subscriptions, billing | **Accepted** 2026-08-24 (design), not yet implemented |

---

## ADR-0000 — the eleven that cannot be revisited casually

These were settled before service code existed, because each is cheap now and expensive or
impossible later.

1. **A user is global; membership is per-organization.** Retrofitting multi-org onto a per-org
   user is the largest migration available.
2. **EdDSA + JWKS; only auth-service mints.** Changing the signing scheme after tokens exist is
   a forced global logout.
3. **Per-audience tokens.** A product token must not work on the operator surface.
4. **No permission list in the token; a `pv` claim instead.** Adding a claim later is a forced
   global logout. A fat token exceeds proxy header limits and fails as a silent 500.
5. **Permission grammar `product.module.resource.action:SCOPE`.** Once customers author custom
   roles, renaming this is a migration of *customer* data.
6. **Event envelope v1 with correlation and causation.** Consumers built on a thin envelope can
   never be traced afterwards.
7. **Money in minor units.** `Numeric(_, 2)` breaks on JPY and KWD and on exact proration, and
   is unfixable once invoices exist.
8. **Plan versions immutable; subscriptions pinned to a version.** Otherwise one price edit
   re-prices every customer and makes past invoices unreconstructable.
9. **Shared schema + `organization_id` + RLS.** Never a database per tenant. Determines whether
   isolation is a query concern or connection routing.
10. **UUID preservation for migrated PulseBoard data.** The difference between one column and
    ninety-one models.
11. **PII key management and rotation.** Rotating with encrypted rows already in place is a
    project, not a task.

## ADR-0001 — one app per surface

Each product and each console is a standalone frontend rather than one monolithic SPA with
routes. Next.js where SEO and static rendering matter (marketing), Vite + React for signed-in
apps. Shared code lives in `atmora-ui`, `atmora-client` and `atmora-config`.

The cost is real — more build config, a risk of visual drift — and the mitigation is the shared
packages. Both are recorded in the ADR.

## ADR-0002 — row-level security (accepted, not implemented)

Documents that RLS is inert for three independent reasons: no policies exist, the application
connects as a superuser (which bypasses RLS unconditionally), and the tenant GUC is never set
because `set_tenant` has no callers.

The decision that matters for reviewers: **enabling policies alone is a silent no-op** — it
looks done, tests pass, nothing is enforced — which is worse than the status quo, because the
query filter is currently the only defence and everyone knows it.

Four questions were left open at first and are now answered, two of them by experiment: drop the
nullable column that stood for platform role templates, split the database role per service now,
leave `users` un-policied because Postgres applies a SELECT policy to `INSERT ... RETURNING`, and
land the prerequisites as a slice containing no policies at all.

## ADR-0003 — surfaces and authorization topology

There are three viewers — operator (Atmora staff, cross-tenant), organization admin (their own
org), employee (their own data) — and they are separated by **two different mechanisms**, which
this ADR refuses to conflate. Operator-vs-customer is an **audience + gateway** boundary: the
operator console is its own app on `admin-gateway` with a separate token `aud`, and staff routes
answer **404, not 403**, so the surface will not confirm its own existence to a product token.
Org-admin-vs-employee is **not a separate app** — it is the permission **scope**
(`OWN/TEAM/LOCATION/ORG`) from the grammar, enforced server-side (RLS + `ScopeContext` + a
negative-authz test per endpoint); the same product SPA renders an admin's whole org and an
employee's own record from identical code. A UI gate hides a control; it does not protect data.
Refines ADR-0001: one SPA per **product** (not per service), shared `atmora-ui`/`atmora-client`/
`atmora-config`, an entitlement-driven shell (`atmora-dashboard`), and mobile as one more client
on the mobile audience over the same APIs.

## ADR-0004 — commercial model (accepted design, not implemented)

Commerce is five services split by what each owns and how fast it changes: `catalog` (immutable
plan versions, features, prices), `subscription` (the customer lifecycle state machine, where
entitlement is decided), `metering` (append-only usage — emitted now with `is_billable=false` so
it need not be backfilled later), `billing` (append-only invoices, proration, the credit ledger),
and `payment` (**Razorpay first** — the more constrained provider). **Per-customer custom pricing
is free**, because ADR-0000 §9 already gives it: a bespoke plan version pinned to one
subscription prices one customer without re-pricing anyone else or breaking past invoices. Grants,
discounts and comp-access-with-expiry are **append-only credit-ledger entries** — balance is
`SUM`, "who is on comp and when it lapses" is a query, and money is integer minor units with no
floats. A trial is a subscription **state**, conversion is a transition, and every transition is
an event the operator console reads for revenue, churn and usage. Commerce invents no new
enforcement point: it drives the existing `product.activations` row → `ent` claim → gateway 402.

---

## Decisions recorded outside an ADR

Not everything warrants an ADR, but these shape daily work and are easy to trip over:

- **Commit messages name no AI assistant** — no generated-by footers, no co-author trailers.
- **The `.claude/` build harness is local tooling and is not tracked.** It stays on disk; the
  backlog it used to carry now lives in [backlog.md](backlog.md).
- **Tests run against real PostgreSQL, never SQLite.** The schema leans on `citext`, partial
  unique indexes and JSONB; a test passing on SQLite proves nothing about production.
- **Each service is run, tested and typechecked in its own process,** because every service
  exposes a top-level `app` package and only one can be importable at a time.
