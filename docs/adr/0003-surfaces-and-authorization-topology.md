# ADR-0003 — Surfaces and authorization topology: who sees what, and where that is decided

**Status: Accepted 2026-08-24.**

Refines [ADR-0001](0001-frontend-topology.md) (one app per surface) and builds on decisions
3 (per-audience tokens), 5 (the permission grammar) and 6 (the scope ladder) of
[ADR-0000](0000-phase-0-decisions.md). Supersedes nothing.

---

## Context

The platform has three kinds of viewer, and they are routinely confused for one another because
two of them can look similar on screen:

1. **The operator** — OpsGrids staff. Sees *across* organizations: every customer, their
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

The operator console is its own frontend (`opsgrids-internal` in ADR-0001), served from its own
origin (`internal.opsgrids.com`), and it talks only to `admin-gateway`. `admin-gateway` and
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
`opsgrids_directory` role (BYPASSRLS, SELECT-only on a named subset of tables) that
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

### 4. `opsgrids-admin` is a task surface, not a third authorization tier

ADR-0001 lists `opsgrids-admin` (customer administration — users, teams, roles, billing) as a
distinct app from the product SPAs. That split is about **ergonomics and ownership**, not
security: `opsgrids-admin` is where a customer configures things that span products, so it does not
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
- **Shared packages, not a shared shell:** `opsgrids-ui` (the design system — the mint/Manrope
  "PlanIQ" language), `opsgrids-client` (auth, gateway wiring, entitlement and permission hooks),
  `opsgrids-config` (build presets). A product app inherits auth, entitlements, permissions, the
  design system and typed errors; it re-implements none of them (ADR-0001).
- **A signed-in shell that authenticates once:** `opsgrids-dashboard` is where a user lands after
  login. It shows only the products the organization has **activated** — entitlement-driven, from
  the same `ent` claim the gateway gates on — and hands off to each product SPA, which is
  independently deployable so a CRM release cannot break HRMS.
- **The operator console (`opsgrids-internal`) is its own app on its own audience** through
  `admin-gateway`, per §2. It is not `opsgrids-dashboard` with extra tabs and not `opsgrids-admin`
  with a staff flag.

### 6. Mobile is another client, not another backend

A mobile app is one more client on the **mobile audience**, talking to the same `app-gateway`
routes and reusing the `opsgrids-client` request/response shapes and the same entitlements. Per
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
- The operator's `opsgrids_directory` bypass is a real, if narrow, cross-tenant surface. It inherits
  ADR-0002's obligations: the route-blast-radius test, and `record_sensitive_read` audit on the
  cross-tenant reads — which the backlog notes still exists and is not yet called. That gap is
  inherited here, not closed by it.
- The mobile audience must be minted by auth-service and accepted by `app-gateway` like any other;
  until it is, "mobile is just another client" is a design commitment, not a running fact. It costs
  a `GatewayPolicy` and an audience, not a service.

---

## Addendum 2026-09-23 — operator roles are a second axis inside the operator boundary, and MFA
is mandatory on it

§2 drew the operator/customer boundary (audience + gateway) and left everything *inside* it as
one undifferentiated `stf` claim. That was fine while the console had one screen; it stopped
being fine once billing overrides, catalog authoring and cross-tenant PII reads all sit behind
the same gate; "any operator" is not a role. This closes that gap without inventing a third
mechanism: `StaffAccount.operator_roles` (`services/auth/app/models.py`) already existed and was
already minted into the token's `roles` claim (`tokens.mint_access`, `staff=True`); nothing
checked it. Two things change:

**1. `platform_core.auth.require_operator(roles={...})`** is the operator-side `require()`.
Every `staff_only` route now declares it, the same way a tenant route declares
`require(permission, minimum=...)`. It keeps §2's 404-for-wrong-audience (a product token must
learn nothing) and adds a plain **403** for right-audience-wrong-role — the surface's existence
is already conceded once the audience check passes, so there is nothing left to protect by
lying about the response code.

**2. Staff accounts require MFA, unconditionally** — not opt-in the way a tenant's
`Credential.mfa_enforced` is. `services.operator_login` never completes on a password alone: an
operator with no confirmed TOTP factor can reach only `POST /auth/operator/mfa/enrol` (and its
`/confirm`), never a usable access token, until enrolment finishes. There is no equivalent of a
tenant's "sign in once, then optionally turn MFA on from inside the session" — the account that
can suspend a subscription or read across every tenant does not get a grace period.

### The role matrix

Three operator roles, defined against **what an operator can act on**, not against a tenant's
permission grammar (ADR-0000 §5) — these never touch `pv` or a membership's grants.

| Capability | `owner` | `billing_ops` | `support` |
|---|---|---|---|
| Everything below | ✅ | | |
| Organization list / detail (`/admin/organizations`, `/admin/organizations/{id}`, and account's `/admin/directory/organizations/{id}`) | ✅ | ✅ | ✅ |
| Platform metrics overview (`/admin/metrics/overview`) | ✅ | ✅ | |
| Audit trail (`/admin/audit`) | ✅ | | ✅ |
| Subscriptions, usage rollups | ✅ | ✅ | |
| Overrides (suspend, comp credit) | ✅ | ✅ | |
| Credit ledger (billing's staff route) | ✅ | ✅ | |
| Catalog writes (plans, versions, publish) | ✅ | | |
| `GET /auth/operator/me` | ✅ | ✅ | ✅ |
| Issue a staff account's enrolment code (`POST /auth/operator/enrollment-codes`) | ✅ | | |

No row exists yet for a staff *catalog read* — there is no such route today, only the writes
above; when one is added it belongs in this table before it ships, not after, per MINOR 9 of the
2026-09-24 security review that found this table describing capabilities the code did not
actually enforce (metrics overview had drifted to look support-readable here when the route was
`owner`/`billing_ops`-only in code, and this row named a route that never existed).

`owner` is not "every permission that exists" as a stored list — it is every `require_operator`
call site accepting it, which is the operator-side reading of ADR-0000 §5's corollary: a role is
built from the catalogue of actions that exist, not asserted as "all of them" once and left to
rot when a new staff route is added forgetting to include it. A route that forgets `owner` in its
set is a bug caught by the negative-authz test every `staff_only` route now carries alongside the
audience one.

Organization identity reads admit all three roles, because an operator support ticket routinely
starts with "what does this account look like" before anyone knows whether it is a billing or a
catalog question; financial reads (metrics, subscriptions, usage, the credit ledger) and the
audit trail do not, per the table above.

---

## Addendum 2026-09-24 — single sign-on across the app hosts, without a domain-wide cookie

ADR-0001 gives every product one hostname. Decision 3 of ADR-0000 gives every session a
short-lived (900s) access token and a long-lived refresh token in an httpOnly cookie
(`services/auth/app/api/routes.py::_set_refresh_cookie`). Nothing before this addendum connected
those two facts to what a person actually experiences: the access token expiring every ~15
minutes signed them out of whatever app they were in, in every open tab, because the shared web
client (`@opsgrids/client`) never called `/api/v1/auth/refresh` — a 401 went straight to the
unauthorized handler and the guard's redirect to `/login`. And because each product SPA has its
own `/login`, a session on one app bought nothing on the next: eleven separate sign-ins for one
person at one organization.

**1. Silent refresh is the client's job, not a server change.** `@opsgrids/client` now tries
exactly one single-flight `POST /auth/refresh` on a 401 (via `auth/refresh.ts`), retries the
original request once, and only signs out if the refresh itself fails. The same function runs
proactively ~60s ahead of the access token's own `exp` (decoded client-side, `auth/token.ts` —
never trusted for anything but *when to ask again*) and once on boot when no token is held in
memory, so a reload or a brand-new tab restores the session instead of bouncing to `/login`. This
alone fixes the 15-minute sign-out, in every app, because every app already depends on this one
package.

**2. The refresh cookie stays host-only on `app.opsgrids.com` — deliberately not a
`.opsgrids.com` cookie.** A domain-wide cookie is the obvious shortcut and the wrong one: the
same registrable domain also carries every merchant's storefront (`*.stores.opsgrids.com`,
ADR-0012), and a merchant page is never supposed to be able to read or ride on a platform
session, including through a stored-XSS path the Caddyfile's own MinIO-serving comments already
flag as a live concern for this registrable domain. A host-only cookie put down on `app.opsgrids.com`
never reaches a storefront host at all, regardless of what runs there. The cost is that every
*other* product SPA cannot refresh same-origin — so:

- Every product SPA except the dashboard and the storefront buyer bundle calls
  `https://app.opsgrids.com/api/v1/auth/refresh` and `/auth/logout` **cross-origin**
  (`auth/origin.ts`'s `configureAuthOrigin`, set from `VITE_AUTH_ORIGIN`, baked in at build time
  by `infrastructure/edge/publish-web.sh` from the box's own `APP_BASE_URL` — the same host,
  never a second config key that could drift from it). The cookie still rides along: two hosts
  under `opsgrids.com` are cross-*origin* but same-*site*, and `SameSite=Lax` only withholds a
  cookie on a genuinely cross-*site* request.
- A product app's own `/login` route (`RedirectToCentralLogin` in `@opsgrids/client`) sends the
  browser to `https://app.opsgrids.com/login?next=<its own URL>` instead of rendering a form —
  sign-in only ever happens on the one host the cookie can be set from.
- The dashboard's login screen redirects back to `next` only after checking it against
  `isAllowedRedirectTarget` (`@opsgrids/client`'s `sso/platformHosts.ts`): an absolute `https://`
  URL, no port, no userinfo, whose host is *exactly* one of the platform's own app hosts. Every
  other shape — a storefront subdomain, a merchant's custom domain, `javascript:`, a
  protocol-relative value, a userinfo trick — is silently ignored in favour of the ordinary
  in-app destination. `tests/infra/test_sso_redirect_allowlist.py` pins that host list against
  the Caddyfile's named sites and `TLS_PLATFORM_HOSTS`, so it cannot drift from what is actually
  deployed without a failing test naming which side moved.
- Logging out from any app calls the same cross-origin `/auth/logout`, so every other open tab
  and app fails its next refresh and lands back on `/login` — one logout ends the session
  everywhere, the same property a single `sid` already gave organization switching (§ above,
  `services/auth`'s session model).

**3. CORS for those two routes is narrow, not a widened `CORS_ORIGINS`.**
`ServiceSettings.auth_refresh_cors_origins` (`platform_core.config`) is a second allow-list,
consulted only for `/api/v1/auth/refresh` and `/api/v1/auth/logout`
(`platform_core.app.AUTH_CROSS_ORIGIN_PATHS`, `_PathScopedCorsMiddleware`); every other route on
app-gateway still answers only `CORS_ORIGINS` (`https://app.opsgrids.com` in production).
Widening `CORS_ORIGINS` itself was rejected: it would let every product origin make a
*credentialed* call to any route the gateway fronts, not just the two that need it — a strictly
bigger blast radius for a page compromised on one of those origins. Storefront hosts and custom
domains must never appear in either list.

**4. A short reuse-grace window on refresh rotation, because the cookie is shared by every tab.**
`rotate_refresh` (`services/auth/app/services.py`) already rotates the refresh token and treats
a spent one turning up again as theft — revoking the whole family. That is correct against an
attacker and wrong against two tabs sharing one cookie: a proactive refresh in one landing
mid-flight of a 401 retry in another presents the *same* token within milliseconds of each
other, and the loser would previously have been signed out along with the winner. A refresh
token presented again within `refresh_reuse_grace_seconds` (30s) of its own rotation — and only
while its family is still live, never once revoked — gets back the successor already minted for
it, read from a short-lived cache entry (`platform_core.cache`) keyed by the spent token's own
`jti`; anything older, or a cache miss (Redis degrades to the stricter path, same as every other
soft cache here), still reads as reuse. The trade this accepts: a thief who also replays within
that 30-second window gets the same successor the legitimate holder does — the documented cost
of any grace window on rotation-based reuse detection, not a gap specific to this one.

**5. CSRF on the two cookie-authenticated routes.** `/auth/refresh` and `/auth/logout` are the
only routes a browser will act on using nothing but the cookie — every other route needs a
bearer token a cross-site page cannot attach. Because a storefront host is same-*site* with
`app.opsgrids.com`, `SameSite=Lax` alone does not stop a bare `<form>` POST from a compromised or
malicious storefront page from carrying the cookie there. Both routes now require
`X-Requested-With: opsgrids` (`services/auth/app/api/routes.py::_assert_not_cross_site`) before
touching the cookie — a header a plain form cannot add, whatever site it is served from.

**Why not just widen the cookie and skip 2–5:** considered and rejected for the reason in §2 —
the registrable domain is shared with every merchant's storefront, and there is no scope
narrower than "every subdomain" a cookie's `Domain` attribute can express. Centralising refresh
on the one host that already holds the cookie, and treating every other origin's access to it as
an explicit, narrow, audited exception, keeps the blast radius of a compromised product SPA (or
storefront) to that one SPA rather than to the platform session itself.
