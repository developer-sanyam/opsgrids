# ADR-0007 — How a new permission reaches a role that already exists in a live tenant

**Status: Proposed 2026-09-16.** A platform capability, discovered while specifying
[ADR-0006](0006-approval-engine.md) §11 and blocking it. Implemented in `account-service` plus one
command in `tools/`. No new service, no new table.

Builds on [ADR-0000](0000-phase-0-decisions.md) §4 (the `pv` permission-version claim), §5 (the
permission grammar and its corollary: a product's admin role is built from that product's own
catalogue) and §10 (shared-schema tenancy with RLS); and on
[ADR-0003](0003-surfaces-and-authorization-topology.md) §3 (authorization is server-side).
Supersedes nothing. **ADR-0006 §11 depends on this ADR**: without it, every `core.approval.*`
endpoint returns 403 to every principal in every existing organization, and the unassigned-request
queue has no holder anywhere.

---

## Context

The platform has a clean story for *declaring* a permission and no story at all for *delivering* one
to a tenant that already exists. This was not visible until a service tried to add permissions to
`core`, which is the one product every organization already has.

What is true today, verified against the code rather than assumed:

| Fact | Evidence |
|---|---|
| `core` is installed **once, at organization creation**, from the **in-code** `CORE_REGISTRATION` | `account/app/services.py:123`, inside `create_organization` |
| `core` is **never** installed from its committed manifest. `libs/contracts/permissions/core.json` has `sources: ["account"]` and no reader | `core.json`; the only manifest readers are `consumers.install_registration_on_activation` and `tools/wire_product.py`, both keyed on a *product activation* |
| There is no `core.product.activated` event for `core`. It is not a marketplace product — it is the platform | `account/app/consumers.py` binds `EVT_PRODUCT_ACTIVATED` and the entitling subscription events; nothing emits one for `core` |
| **`install_registration` skips any role code that already exists** — the single line this ADR exists to fix | `account/app/services.py`: `if await selectors.get_role_by_code(session, org_id, template.code): continue` |
| A role template whose code is **new** *is* created correctly in a live tenant. Only *existing* codes are skipped | same loop: the `continue` is per template code |
| `install_product_registration` — the activation path, and the path `tools/wire_product.py` drives — calls that same installer. It adds **roles to owners**, never **grants to roles** | `account/app/services.py` `install_product_registration` |
| A grant naming a permission row that does not exist is **silently dropped**, not an error | same installer: `permission = await get_permission_by_code(...)`, `if permission is None: continue` |
| The public roles API **refuses to edit a system role**, so an operator cannot widen `MEMBER` through it either | `replace_role_grants`: `if role.is_system: raise BusinessRuleProblem(...)` |
| System roles are platform-authored (`is_system=True`, set only by `install_registration`); customer-authored roles are `is_system=False` | `install_registration` sets it True; `create_role` never sets it |
| `pv` works as ADR-0000 §4 says: `_invalidate_grants` → `bump_permission_version` (a Redis `INCR`), and the gateway refuses `claims["pv"] < current` and resolves grants per request from account, cached against the version | `account/app/services.py`; `platform_core/cache.py:134`; `platform_core/gateway.py:319-320, 359-365` |
| The bump is deliberately **fail-loud**: losing it leaves a revoked grant usable until token expiry | `platform_core/cache.py:138-142` |
| `core.grants.changed` is **published and consumed by nobody** today | `EVT_GRANTS_CHANGED` is emitted in three places; no `consumer.on` binds it anywhere |

So the shape of the hole: **a permission added to an existing role template reaches new
organizations and no existing ones, forever, with no error anywhere.** The tenant's `MEMBER` role
keeps the six grants it was born with. Nothing fails a health check, no migration complains, and the
symptom at the far end is a 403 that looks like a bug in the service that added the permission.

This is not an approval-engine problem. It is the first time anyone hit it, and it will recur on
every catalogue change to any product in every tenant that activated that product earlier.

---

## Decision

### 1. Installing a registration becomes a **reconcile**, not a first-write

`install_registration` gains a second half. For each role template in the registration:

- **Role missing** → create it with its grants. Unchanged behaviour.
- **Role present and `is_system = True`** → **widen it**: insert every `(permission, scope)` grant
  the template names and the role lacks, and raise the scope of an existing grant whose code matches
  but whose scope is narrower than the template's. Nothing is removed, nothing is narrowed, no
  column but the grant set is touched.
- **Role present and `is_system = False`** → **leave it alone entirely.** See §4.

The widen is a set operation over `(permission_code, scope)`, computed in the same transaction that
writes it. Scope comparison uses the existing `_RANK` ladder (`OWN < TEAM < LOCATION < ORG`) and the
permission's own `max_scope` ceiling, so a reconcile can never grant wider than the catalogue allows
— the same guard `PermissionCatalogue.all()` applies at declaration time.

**Why union rather than exact synchronisation.** An exact sync would also *remove* grants a template
no longer names, which is the operation that can silently revoke access across every tenant in one
command, with no per-tenant review and no undo. Union can only ever grant. The trade is stated
plainly: **a permission removed from a template stays in live tenants until somebody writes a
deliberate revocation migration**, and this ADR does not provide one. Revocation is rarer, riskier
and deserves its own review; widening is routine and needs to be boring. If a grant must be pulled
from every tenant, that is a one-off script with an explicit list, written and reviewed for that
occasion.

### 2. `core` gets a manifest path, and the in-code registration is pinned as a **subset** of it

`core.json` becomes installable exactly like any other product's manifest. Organization creation
keeps using the in-code `CORE_REGISTRATION` — it runs before any manifest directory is guaranteed
readable, and a new organization must not depend on a file lookup. The reconciler for existing
organizations reads `core.json`.

Two sources for one thing is a drift risk, so it is pinned by a test — but **not** as an equality,
and an earlier draft of this section was wrong to say so.

**Corrected — the two are not equal, and by design never will be again.** `core` is a *shared
platform product*: `tools/export_permissions.py` merges every contributing service's slice into one
manifest per product. The moment `services/approval/app/catalogue.py` declares its slice of `core`
(ADR-0006 §11), `core.json`'s `sources` is `["account", "approval"]` — exactly the multi-service
merge `hrms.json` already carries with `["attendance", "employee"]`. The merged manifest is therefore
a **strict superset** of account's in-code `CORE_REGISTRATION`, permanently. Verified in the repo
today: the manifest carries the four `core.approval.*` permissions and a fourth role template that
account's in-code registration does not, and cannot, contain.

**What is pinned is a subset property:**

- every permission code and every role-template code in `CORE_REGISTRATION` appears in `core.json`;
- no grant in the manifest is **narrower** than the one `CORE_ROLE_TEMPLATES` declares for the same
  role and permission.

The manifest may be wider. It may never be smaller or weaker. That catches the drift that actually
matters — a permission or template dropped from the manifest, or silently narrowed, while the
in-code path keeps promising it to every newly created organization — and it holds no matter how
many services later contribute to `core`.

**Equality could only ever have held while a product had exactly one contributing service.** For
`core` that stopped being true the moment ADR-0006 landed, so the assertion was a build failure
waiting for the second contributor, and it duly failed in the build. The general rule, worth stating
because the next shared product will hit it: **a manifest is a merge, so pin the direction that
protects the guarantee, not the shape of the file.**

The stronger guard is behavioural rather than structural: an integration test reconciles a real
organization against the committed `core.json` and asserts the outcome this ADR exists to produce —
`ORG_OWNER` ends up holding `core.approval.request.decide:ORG` and `request.read:ORG`, `ORG_ADMIN`
holds `policy.write`, `MEMBER` holds the OWN pair, and `APPROVAL_ADMIN` installs as a new system
role. That test would have caught the original gap; a file-shape comparison would not.

Installation was always right and is unchanged: the reconciler loads the **merged** manifest
(`load_product_manifest("core")`), so a tenant receives every contributor's slice, not just
account's. `tools/export_permissions.py --check` still fails the build when a committed manifest is
stale, which is the other direction of the same guard. Account does not import approval's catalogue
and cannot; the manifest is the seam.

### 3. Who runs it, and when: a one-shot command in the deploy that ships the permission

```
uv run python tools/reconcile_permissions.py <product_key> [--org <id> | --all] [--dry-run]
```

The same shape, the same installer and the same connection discipline as `tools/wire_product.py`:
the NOBYPASSRLS app role, `session_scope(organization_id=…)` bound per organization, **one
transaction per organization**. Platform staff run it; it is deliberately **not** an API, because it
writes to every tenant and that is a reviewed deploy step, not a customer action.

It is **not** run automatically at service startup and **not** driven by a consumer. A reconcile on
every boot is an unreviewed write to every tenant on every restart, and it would make a rollback of
the service that declared the permission leave the grants behind with nothing enforcing them.

`--dry-run` prints the diff it would apply, per organization, and writes nothing. It is the expected
first invocation.

**The ordering is load-bearing**, because of the silent-skip trap in the Context table — a grant
naming a permission row that does not exist is dropped without error:

1. Merge the catalogue change and regenerate the manifests (`tools/export_permissions.py`).
2. Deploy the service that **enforces** the new permission.
3. Run the reconciler (`--dry-run` first, then for real).
4. Only then turn on the UI that assumes the permission exists.

Reversed, step 3 before step 1 writes roles that are missing exactly the grants the whole exercise
was for, and reports success.

### 4. Customer-authored roles are never touched, and the gap is **reported** rather than closed

A role with `is_system = False` is customer data (ADR-0000 §5: once customers author roles, the
permission code is customer data). The platform does not add grants to it. A customer who built
"Shift supervisor" from scratch decides for themselves whether it should be able to approve things,
through the ordinary roles API.

The consequence is real and must not be discovered in a support ticket: **a tenant that replaced
`MEMBER` with a role of their own gains nothing from the reconcile.** So the reconciler reports it.
Per organization it prints, and the summary carries:

```
{org_id, roles_created, grants_added, scopes_widened, memberships_without: <n>}
```

where `memberships_without` counts active memberships holding **no** role that carries the newly
added permission codes. It is a number an operator can act on, not an automatic fix — automatically
granting a new capability to a role a customer authored is precisely the "HR admin holds every
permission" failure ADR-0000 §5's corollary exists to prevent.

For ADR-0006 specifically this is why the unassigned queue is safe: `ORG_OWNER` is a system role, it
is reconciled, and its template is `CORE.all("ORG")` — so every organization ends the reconcile with
at least one holder of `core.approval.request.decide:ORG`.

### 5. Idempotency and the `pv` bump

A second run computes an empty diff, writes nothing, bumps nothing and exits zero. That property is
what makes the command safe to re-run after a partial failure, and it is the reason the bump is
conditional rather than unconditional:

- **Bump `pv` for an organization only when that organization's diff was non-empty** (`grants_added
  + scopes_widened > 0`). Creating a role nobody holds, or installing permission rows, changes what
  *exists*, not what anybody *may do*. This is the rule `install_product_registration` already
  applies (`if granted: await _invalidate_grants(org_id)`), extended to cover widening.
- The bump happens **inside** the organization's transaction, matching `_invalidate_grants`'s
  existing reasoning: if the transaction then rolls back, the counter has still moved, which costs
  one round of token refreshes and grants nobody anything. That is the safe direction to be wrong in.
- `bump_permission_version` **raises** when Redis is unreachable. An organization whose bump fails
  fails the whole organization and is re-run; it is never reported as reconciled. A widening that is
  written but not announced would sit invisible until every token in the tenant expired.

**What the bump costs, stated because it is the user-visible cost of this ADR.** Bumping `pv`
invalidates every access token in that organization; the gateway refuses them and every client
refreshes. For a 4,000-person tenant that is a refresh burst at the instant of the reconcile. So:
the fleet run (`--all`) is **sequential, one organization per transaction, with a short pause
between organizations** (default 250 ms, `--pace`), which spreads the bursts instead of stacking
them, and the command is run outside peak hours like any other fleet write. A parallel reconcile is
rejected: it buys minutes on a job that runs a handful of times a year and concentrates the refresh
storm it exists to avoid.

**No per-membership event fan-out.** `core.grants.changed` is published per membership by the
interactive paths; a widening touches every membership in the tenant, so the same fan-out would emit
thousands of events per organization. It has **no consumer today** (verified), and `pv` — not the
event — is the mechanism that makes a grant change take effect. The reconciler therefore publishes
**one** `core.grants.changed` per organization, with `{organization_id, role_codes, permission_codes}`
and no `membership_id`, as a durable record on the bus for whoever consumes it later. If a consumer
ever needs per-membership granularity it can expand the role locally; that is a cheaper problem than
a fan-out nobody reads.

### 6. Audit

One audit row per organization per reconcile, written in the same transaction as the change:
`action = "permissions.reconciled"`, `entity = "organization"`, `actor_id = NULL`,
`acting_as = "reconcile-permissions-cli"`, with `before`/`after` carrying the affected role codes and
their grant sets — the same shape `role.grants_changed` already writes, so an auditor reading the
trail sees platform-driven and customer-driven grant changes in one vocabulary. An empty diff writes
no audit row: "nothing happened" is not an event.

---

## What this rejects

- **Leaving it to per-tenant SQL.** It is what would happen by default, it is unauditable, it is not
  idempotent, and it silently skips the `pv` bump — so the grant lands in the database and no token
  can see it until it expires.
- **Making `install_registration` upsert grants for *every* role, including customer-authored ones.**
  One line shorter, and it hands customers' own roles a capability they never chose. That is
  ADR-0000 §5's corollary violated from the other end.
- **Exact synchronisation (remove grants the template no longer names).** One command that can
  revoke across every tenant, with no per-tenant review (§1).
- **Reconciling automatically at service startup, or from a consumer.** An unreviewed write to every
  tenant on every restart, and it strands grants when the service that declared them is rolled back.
- **An API endpoint for reconciliation.** A cross-tenant write does not belong on a customer-facing
  surface; ADR-0002 §2's reasoning about not widening bypass surfaces for one-time jobs applies to
  authority as much as to RLS.
- **Per-membership `core.grants.changed` fan-out on a fleet widening** (§5).
- **Auto-granting the new permission to customer-authored roles to "make it work everywhere"** (§4).
- **A new `APPROVAL_ADMIN`-style template solving this on its own.** A brand-new role code *does*
  install correctly into a live tenant — that part was never broken — but nobody holds it on the day
  it lands, so it cannot be the mechanism by which an existing `MEMBER` gains `decide:OWN`.

## Consequences

- **ADR-0006 becomes shippable.** Its §11 grants reach live tenants in the deploy that ships them,
  and `ORG_OWNER` always holds `decide:ORG`, so the unassigned queue always has an owner.
- **Every future catalogue change gets a rollout step.** Adding a permission is no longer "edit
  `catalogue.py`"; it is edit, export, deploy, reconcile. That step is a line in the PR description
  and is easy to forget, so the reconciler's `--dry-run` output is the expected evidence in review.
- **Tenants that authored their own roles will lag**, visibly, by design. `memberships_without` is
  the number that says how much, and closing it is a conversation with that customer.
- **A grant can never be withdrawn by this mechanism.** The union rule means the only way back is a
  bespoke revocation script. Accepted knowingly (§1).
- **A fleet reconcile is a token-refresh event.** Paced and off-peak, it is a burst of refreshes, not
  an outage — but it is not a silent operation and should not be run casually.
- **Two representations of `core` exist** (in-code and manifest), and they are deliberately **not**
  identical: the manifest is the merge of every contributing service's slice, so it is a strict
  superset of the in-code registration and grows with each new contributor (§2). They are held
  together by a subset pin plus a behavioural reconcile test, never by a file comparison. That is a
  seam to watch; if it drifts in practice despite the pin, the answer is to install `core` from its
  manifest at organization creation too, and delete the in-code copy.

## Revisit when

- **A permission must be removed from every tenant.** This ADR deliberately has no answer; that
  occasion needs its own decision about narrowing and about what a narrowed token does mid-session.
- **The fleet is large enough that a sequential paced reconcile takes too long to sit in a deploy
  window.** Then the unit of work becomes a queue drained by a worker, with the same per-organization
  transaction and the same conditional bump — not a parallel command.
- **A customer asks for their own roles to track platform templates.** That is an opt-in flag on a
  role (`follows_template`), and it changes §4. It needs a real customer asking, not an imagined one.
- **A second mechanism starts writing grants** (an entitlement system, a marketplace add-on). Then
  "who may write a grant" needs one owner, and it is this path plus the roles API today.
