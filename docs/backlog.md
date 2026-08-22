# Backlog

What is next, in order, and what is blocking. Delivered work is at the bottom.

> This list previously lived in the build harness's ledger under `.claude/`, which is local
> tooling and no longer tracked in the repository. It lives here now so it ships with the code.

Each item is a **slice**: a capability that crosses the whole stack — schema, API, UI, tests —
rather than a horizontal layer. Nothing is "done" until its tests pass.

---

## Blocked, and first

### Row-level security
**[ADR-0002](adr/0002-row-level-security.md) is accepted and its four open questions are
answered.** In progress, in two slices.

**Slice one — prerequisites, no policies: DONE** (`c708f60`, `<pending>`).

Services connect as per-service NOBYPASSRLS roles and migrations as an owner role; grants are
applied by the migration run so a new service cannot ship without them; TRUNCATE and
audit-rewrite are withheld by privilege; the test suite runs as the app role so the split is
visible to it. The tenant chokepoint opens each request's transaction and binds it to the
caller's organization as the first statement — routes no longer open transactions of their own.

Exit gate met: the GUC is set on every transaction, it does **not** survive the transaction,
and a static check refuses any route that takes a bare session or opens its own transaction.
Verified by deliberately introducing a bypass and watching the guard fail.

A correction to the record: an earlier commit message said migrations had been "silently doing
nothing". They had not — `alembic` exits 255 on failure and `make migrate` surfaces it. That
claim came from reading the exit code of `tail` through a pipe rather than of `alembic`.

**Slice two — policies: DONE.**

Row-level security is live on the `account` schema. Eleven tables carry a `tenant_isolation`
policy with both `USING` and `WITH CHECK`; `users`, `permissions` and `processed_events` are
exempt for the reasons ADR-0002 records. `roles.organization_id` is NOT NULL and the template
machinery is gone. The cross-tenant reads that sign-in depends on run on a third role —
BYPASSRLS but SELECT-only on seven identity tables — so they keep working without handing the
migration role to a module that only reads.

`test_cross_tenant_isolation.py` is the acceptance test: a bound session sees only its own rows,
cannot read another tenant's row by primary key, cannot write into another tenant, and an unbound
session sees nothing at all.

**Still open here:** the generated per-route cross-tenant sweep, and the index recomposition
ADR-0002 lists (`audit_logs` first — its five single-column indexes no longer lead with the
tenant column now that the policy qual is ANDed into every query). Neither blocks correctness;
the second is a performance cliff waiting for volume.

---

## Next

### End-to-end journey suite
Boot the services together and run one real journey: create an organization → invite → sign in
→ reach an entitled endpoint → be refused an unentitled one → be refused another tenant's data.

This is high value because **the two most recent real defects were both invisible to unit
tests** — each side stubbed the other. Until this exists, "194 tests pass" says less than it
appears to.

### `product-service`
Product registry, per-org activation, entitlements, marketplace catalogue. Turns the gateway's
entitlement gate from *a claim the token happens to carry* into something backed by a registry.

Start emitting usage events here with `is_billable=false`. Turning on usage-based pricing later
then becomes plan configuration rather than an architecture change, and it costs almost nothing
now.

### `config-service`
Per-org settings, integration secrets, feature toggles.

---

## After that

| Slice | Why it is where it is |
|---|---|
| Platform services — document, notification, audit, scheduler, eventbridge | Every later product gets files, email, audit and cron without building them |
| **Event consumers** | The outbox produces and nothing consumes. Traced flows stay theoretical until handlers exist |
| HRMS rebuild — employee, attendance, leave, payroll, recruit | Fix the known timezone defect **during** the rebuild rather than porting it |
| PulseBoard rebuild + data migration | **Riskiest slice** — live customer data, preserved UUIDs. Needs a *rehearsed* rollback, not a written one |
| CRM greenfield — customer, sales, marketing, support | CRM owns Customer/Contact; PulseBoard's clients migrate onto it |
| Billing — catalog, subscription, metering, billing, payment | **Razorpay adapter first** |
| Ecosystem surface — super-admin console, marketplace, workflow, analytics | |

**Why Razorpay before Stripe.** It is the more constrained provider: mandate caps, a legally
required pre-debit notice, multi-day eMandate activation, no true idempotency key. An interface
designed Stripe-first produces abstractions Razorpay cannot satisfy — discovered after it has
ten call sites.

---

## Known gaps not yet scheduled

- **The internal service token is a shared static secret** in a header, defaulting to
  `dev-only-internal-token`. One leak reads every user, membership and organization name on the
  platform. Needs a short-TTL signed service token or mTLS, plus a network policy making
  `/internal/v1` unreachable from ingress.
- **`record_sensitive_read` exists and is never called** — the cross-tenant internal reads are
  the highest-value reads in the system and currently leave no trace.
- **No outbox-lag probe.** A stalled relay is silent. Worth adding before RLS lands, so there is
  a baseline to compare against.
- **SSO is modelled but has no flow** — `org_idp_configs` exists; no OIDC or SAML implementation.
- **10 of the 18 quality-gate rows have no tooling** — production build, E2E, schema drift,
  visual regression, automated accessibility, performance budget, query plans, SAST and licence,
  dependency boundaries, token lint.
- **`atmora-ui` and `atmora-client` are empty** workspace packages.

---

## Delivered

| Slice | Commit |
|---|---|
| Platform foundation — `platform-core`, dev bootstrap, CI, service generator | `659ea5b`, `90ed777` |
| Marketing website + frontend workspace | `c788ab0` |
| account-service — orgs, units, locations, users, memberships, roles, invitations | `4210187`, `cf858eb` |
| auth-service — credentials, sessions, refresh rotation, MFA, SSO config, keys | `a6e3548` |
| Gateways — `app-gateway` and `admin-gateway` | `ca58de3` |
| Internal API + permission-version bump | `15d6ec0` |

Two of those closed defects that would have been outages in production: the gateways shipped
with a **duplicated correlation header** and an **unenforced per-route `staff_only`** flag, and
account-service was **serving none of the internal endpoints** that auth and both gateways call.
