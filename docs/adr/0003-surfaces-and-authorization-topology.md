# ADR-0003 — Surfaces and authorization topology: who sees what, and where that is decided

**Status: Accepted 2026-08-24.**

Refines [ADR-0001](0001-frontend-topology.md) (one app per surface) and builds on decisions
3 (per-audience tokens), 5 (the permission grammar) and 6 (the scope ladder) of
[ADR-0000](0000-phase-0-decisions.md). Supersedes nothing.

---

## Context

The platform has three kinds of viewer, and they are routinely confused for one another because
two of them can look similar on screen:

1. **The operator** — Atmora staff. Sees *across* organizations: every customer, their
   activations, their billing, and the override controls that support and revenue need. This is
   the vendor looking at its tenants.
2. **The organization admin** — the customer's own leader. Sees *everything inside their own
   organization* and nothing outside it: all employees, all deals, the org's users, roles and
   subscription.
3. **The employee** — a seat inside a customer's organization. Sees *their own* data and whatever
   their role widens that to: their record, their team, their location.

Levels 2 and 3 render largely the same screens — an org admin's employee directory and an
employee's "my profile" are the same components against different rows. Level 1 is a different
job entirely. The failure this ADR exists to prevent is treating all three as *roles on one axis*,
because that invites two specific mistakes that a later system cannot easily undo:

- **collapsing the operator into the org** — shipping the cross-tenant surface as "an admin with
  more permissions", reachable with an ordinary product token. Decision 3 of ADR-0000 chose
  per-audience tokens precisely so this cannot happen, but the token design only pays off if the
  surfaces are drawn to match it.
- **promoting the org-admin/employee split into an app or a role flag** — building a separate
  "admin app", or a boolean that swaps which UI a person sees, and letting that stand in for
  authorization. A UI that hides a control does not protect the data behind it.

The mechanisms that decide all of this already exist in the codebase. This ADR records *which
mechanism draws which boundary*, so the answer stops being folklore.

---

## Decision

### 1. Two boundaries, drawn by two different mechanisms

The three levels are separated by exactly two boundaries, and they are **not** the same kind of
thing. Getting this right is the whole ADR.

| Boundary | Between | Drawn by | Enforced at |
|---|---|---|---|
| **Operator ↔ customer** | level 1 and levels 2–3 | **token audience + gateway** | `admin-gateway` vs `app-gateway`, the `aud` claim, the `stf` claim |
| **Org admin ↔ employee** | level 2 and level 3 | **permission scope** | server-side: RLS + `ScopeContext` + a negative-authz test per endpoint |

The first is an **audience-and-gateway** boundary: separate applications, separate token
audiences, separate gateway. The second is **not an application boundary at all** — it is the
`:SCOPE` suffix on a grant, resolved and enforced in the same server code for both levels.

### 2. Operator is a separate app on a separate audience — and staff routes 404, not 403

The operator console is its own frontend (`atmora-internal` in ADR-0001), served from its own
origin (`internal.atmora.com`), and it talks only to `admin-gateway`. `admin-gateway` and
`app-gateway` are the same `build_gateway` code with different `GatewayPolicy` — crucially a
different `audience` (`gateway.py`, `GatewayPolicy.audience`). A token minted for a product carries
that product's `aud` and is **rejected at signature-verification time** on the operator gateway,
before any route or permission is consulted (`gateway.py`, `decode_token(..., audience=policy.audience)`
in the proxy). This is decision 3 of ADR-0000 made physical: *a product token must not work on
the operator surface*, and it does not — not because a check says "you are not staff" but because
the token is not addressed to that gateway.

Within `admin-gateway`, individual staff routes carry `staff_only`, and the check demands the
`stf` claim (`gateway.py`, `if (policy.staff_only or route.staff_only) and not claims.get("stf")`).
**A caller who lacks it gets 404, not 403** — deliberately the same response a nonexistent route
gives (`NotFoundProblem`, "the existence of the operator surface is not something to confirm").
403 would confirm that an operator surface exists at that path and that the caller merely lacks a
role; 404 tells a probing product token nothing. The per-route flag, not just the gateway-wide
one, is what matters here: `admin-gateway` also carries internal service-to-service routes, and a
gateway-wide flag alone would leave the per-route intent in `routes.json` unenforced — a bug the
gateways actually shipped with once, recorded in the backlog.

The operator's cross-tenant reads do not go through RLS as a tenant would. They run on the
`atmora_directory` role (BYPASSRLS, SELECT-only on a named subset of tables) that
[ADR-0002](0002-row-level-security.md) §2 created for exactly this — a *named, auditable* bypass
surface, not a superuser. Operator writes that reach into a tenant follow the "bypass for the
lookup only, then narrow" pattern ADR-0002 established: resolve the organization on the bypass
session, then do every write under a tenant-bound session so the policy still applies.

### 3. Org admin vs employee is scope, enforced server-side — never an app or a role flag

Between level 2 and level 3 there is **one product SPA and one set of endpoints**. What differs is
the `:SCOPE` on the grants the caller holds, from the grammar `product.module.resource.action:SCOPE`
(ADR-0000 §5) on the ladder `OWN < TEAM < LOCATION < ORG` (§6). An HR administrator holds
`hrms.people.employee.read:ORG`; an ordinary employee holds `hrms.people.employee.read:OWN`
(`services/employee/app/catalogue.py`, the `HR_ADMIN` and `EMPLOYEE` templates). The same list
endpoint renders the whole organization for the first and a single record for the second, because
`ScopeContext.apply()` (`platform_core/auth.py`) injects the scope into the query — `ORG` adds no
filter, `OWN` filters to the caller's `subject_ref`. **The admin's org-wide view and the
employee's own record are literally the same code path** narrowed differently, which is the point:
there is nothing to keep in sync between an "admin build" and an "employee build" because there is
only one build.

Two rules from `platform_core/auth.py` make this enforcement rather than decoration, and both are
load-bearing:

- **A permission name without enough scope is not access.** Every employee holds
  `...employee.write:OWN`, so an endpoint guarded only by the permission *name* would let any
  employee create employee records. Actions with no meaningful "own" reading pass
  `minimum=Scope.ORG` to `require(...)`, which floors the scope regardless of the grant. This is
  why the guard is `require(perm, minimum=Scope.ORG)`, not `require(perm)`.
- **Filtering the list is not guarding the object.** `apply()` narrows a `SELECT`;
  `assert_can_touch()` guards a single row on detail and mutate endpoints. Both are needed. A
  system that filtered the list but let a location-scoped caller fetch any object *by id* is the
  named vulnerability the module docstring calls out, and `assert_can_touch` compares the row's
  location precisely so a scope cannot "return early" into a hole.

**Why a UI gate is not enforcement.** The frontend has `useEntitlement(feature)` and
`<RequirePermission perm=…>` (ADR-0001), and they exist to *hide controls the caller cannot use* —
a better experience, nothing more. The browser is the caller's machine; a hidden button is a `curl`
away. Authorization is decided only where the query runs: every list endpoint injects scope into
the query, and — the rule that makes this checkable — **every endpoint carries a negative-authz
test** proving that a caller outside scope is refused. The UI mirrors the server's decision; it
never *is* the decision. RLS (ADR-0002) sits underneath as the net: even a query that forgot to
filter cannot cross the organization boundary, because the tenant is pinned in the transaction.

### 4. `atmora-admin` is a task surface, not a third authorization tier

ADR-0001 lists `atmora-admin` (customer administration — users, teams, roles, billing) as a
distinct app from the product SPAs. That split is about **ergonomics and ownership**, not
security: `atmora-admin` is where a customer configures things that span products, so it does not
belong inside any one product. It is still a **customer** surface on the **product audience**
through `app-gateway`, and reaching it grants nothing on its own — every action it offers is
guarded by the same server-side scope check as everywhere else. The authorization boundary between
an org admin and an employee is their grants, not which of the customer's apps they happen to open.
Do not read the app list as a permission ladder.

### 5. Frontend topology, restated for surfaces

This refines ADR-0001 by naming the axis each split is on:

- **One SPA per _product_, not per service.** Many services compose one product (HRMS is
  `employee`, `attendance`, `leave`, `payroll`, `recruit`); the customer buys and sees one thing.
  The frontend boundary matches the *product*, which is also the `key` that is one string across
  the gateway route, the permission prefix, the `ent` claim and the marketplace slug
  (`services/product/app/models.py`, `Product.key`).
- **Shared packages, not a shared shell:** `atmora-ui` (the design system — the mint/Manrope
  "PlanIQ" language), `atmora-client` (auth, gateway wiring, entitlement and permission hooks),
  `atmora-config` (build presets). A product app inherits auth, entitlements, permissions, the
  design system and typed errors; it re-implements none of them (ADR-0001).
- **A signed-in shell that authenticates once:** `atmora-dashboard` is where a user lands after
  login. It shows only the products the organization has **activated** — entitlement-driven, from
  the same `ent` claim the gateway gates on — and hands off to each product SPA, which is
  independently deployable so a CRM release cannot break HRMS.
- **The operator console (`atmora-internal`) is its own app on its own audience** through
  `admin-gateway`, per §2. It is not `atmora-dashboard` with extra tabs and not `atmora-admin`
  with a staff flag.

### 6. Mobile is another client, not another backend

A mobile app is one more client on the **mobile audience**, talking to the same `app-gateway`
routes and reusing the `atmora-client` request/response shapes and the same entitlements. Per
ADR-0000 §3 the token names the mobile audience in `aud`; everything downstream — scope resolution,
RLS, the 402 gate — is identical, because none of it knows or cares what rendered the request. A
new client platform is **no service change**: it is a new audience the auth-service will mint for
and the gateway will accept, and one more consumer of the same APIs.

---

## What this rejects

- **A single monolith SPA with role-based routing.** Rejected already by ADR-0001 for build and
  deploy coupling; rejected again here because it would put the operator surface one routing bug
  away from a customer, and make "hide the admin routes" look like a security control.
- **A role flag that swaps UIs instead of scopes.** A boolean like `is_admin` that decides which
  screens render puts the authorization decision in the client. The server would then either
  re-derive the same fact (making the flag redundant) or trust it (making it a forgery target).
  Scope on the grant, resolved server-side, is the single source of truth.
- **An operator surface reachable with a product token.** The most dangerous version of the first
  mistake: staff routes behind `app-gateway`, or a `stf`-style claim minted into product tokens.
  The audience split makes it structurally impossible rather than a check to remember, and the
  404-not-403 response refuses even to confirm the surface is there.
- **A separate app as the org-admin boundary.** Building `product-crm-admin` alongside
  `product-crm` would duplicate every screen and still need the server-side scope check underneath,
  so it buys nothing and doubles the drift surface ADR-0001 works to avoid.

## Consequences

- The three levels now map cleanly onto mechanisms that already exist: audience+gateway for the
  operator boundary, scope for the org-admin/employee boundary, RLS underneath both. Nobody has to
  invent a new control to add a surface.
- Adding a product adds a product SPA and its permission catalogue; it does **not** add an admin
  app, a role-flag branch, or an operator surface. The operator console reads new products through
  the same activation and entitlement data every product already produces.
- Because org-admin and employee share code, a scope bug is a single-place bug — and a single-place
  fix. The cost is vigilance at exactly one point: `apply()` for lists and `assert_can_touch()` for
  objects, guarded by the per-endpoint negative-authz test. That test is not optional polish; it is
  what keeps the shared code path honest.
- The operator's `atmora_directory` bypass is a real, if narrow, cross-tenant surface. It inherits
  ADR-0002's obligations: the route-blast-radius test, and `record_sensitive_read` audit on the
  cross-tenant reads — which the backlog notes still exists and is not yet called. That gap is
  inherited here, not closed by it.
- The mobile audience must be minted by auth-service and accepted by `app-gateway` like any other;
  until it is, "mobile is just another client" is a design commitment, not a running fact. It costs
  a `GatewayPolicy` and an audience, not a service.
