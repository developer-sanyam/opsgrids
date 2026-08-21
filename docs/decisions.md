# Decisions

An index of the architecture decision records, and the short version of each. The ADRs
themselves are the authority — this page exists so you can find the right one quickly.

| ADR | Title | Status |
|---|---|---|
| [0000](adr/0000-phase-0-decisions.md) | Phase 0 — eleven irreversible decisions | Accepted |
| [0001](adr/0001-frontend-topology.md) | One standalone app per surface | Accepted |
| [0002](adr/0002-row-level-security.md) | Row-level security | **Proposed — under review** |

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

## ADR-0002 — row-level security (proposed)

**Under review. Do not implement it yet.**

Documents that RLS is inert for three independent reasons: no policies exist, the application
connects as a superuser (which bypasses RLS unconditionally), and the tenant GUC is never set
because `set_tenant` has no callers.

The decision that matters for reviewers: **enabling policies alone is a silent no-op** — it
looks done, tests pass, nothing is enforced — which is worse than the status quo, because the
query filter is currently the only defence and everyone knows it.

Four questions are deliberately left open, including whether to build platform role templates or
drop the nullable column that represents them.

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
