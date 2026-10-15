# ADR-0008 — Who writes `memberships.employee_id`: the link that gives a principal an employee identity

**Status: Proposed 2026-09-17, implemented the same day, amended in place after that
implementation review.** Implemented in `account-service` (one schema field, one service rule, one
list filter), `employee-service` (one internal read endpoint, one refused write) and one command in
`tools/`. No new service, no new table, no new permission, no new event. §9's third surfacing item
landed in `services/approval` the same day, so all three surfacings are delivered; what remains open
is listed under "Revisit when" and in §8's note that nothing yet *schedules* the reconcile.

Builds on [ADR-0000](0000-phase-0-decisions.md) §1 (a user is global, membership is per-organization
and carries the employee link), §3 (tokens are per-audience and carry no permission list), §4 (`pv`),
§6 (the scope ladder and its per-product resolvers) and §10 (shared-schema tenancy, RLS, **no
cross-schema foreign keys**, a nightly orphan check); on
[ADR-0002](0002-row-level-security.md) §2 (do not widen a bypass surface for a one-time job); on
[ADR-0003](0003-surfaces-and-authorization-topology.md) §3 (authorization is enforced server-side at
the data-access boundary); on [ADR-0006](0006-approval-engine.md) §1 (neither side calls the other
synchronously), §2 (ordering an asynchronous copy needs a monotonic version), §4 and §6 (what a
principal with no `subject_ref` may do); and on
[ADR-0007](0007-permission-rollout-to-live-tenants.md) §3 (a reviewed one-shot reconcile command) and
§5 (what a `pv` bump costs).

**Supersedes nothing, and deliberately does not amend ADR-0006 §6.** §9 below explains why the
silently-empty approval inbox stays silently empty.

> **Amended 2026-09-17, after the implementation review.** The implementation found three defects in
> this document rather than in the code, and each is corrected in place with an **Amended** note
> saying what the earlier text claimed, so a reader knows the claim was retracted rather than quietly
> replaced. **§8** said three mutually exclusive things about transaction granularity — it now says
> one, and rejects the two alternatives by name, including the memory-saving one nobody should
> reach for. **§8**'s report contract gains a counter (`unrecognised_membership_ids`) and a
> `conflicts` array, and records a decision the ADR left to the engineer: the reconcile does not fill
> a *removed* membership's link. **The Consequences** credited a mitigation that does not exist —
> `is_dangerous` has no effect on request-time authorization — leaving two real mitigations, not
> three; the same correction is carried to §2 and to ADR-0000 §5, where it belongs in front of every
> future ADR author rather than buried here. **§9** now names an owner for each of its three
> surfacing items, because the third one belongs to a service neither implementing engineer could
> touch.

---

## Context

`sref` is the claim that answers "which person is this, in the domain the product cares about". Every
`OWN`-scoped read and every `OWN`-scoped write in the estate resolves against it. It has exactly one
origin, and that origin has no writer.

What is true today, verified against the code rather than assumed:

| Fact | Evidence |
|---|---|
| `account.memberships.employee_id` is a plain nullable UUID, deliberately not a foreign key because the link crosses a schema boundary | `account/app/models.py:278` and its comment; ADR-0000 §10 |
| It carries a **partial unique index** — one employee, one membership, per organization | `uq_memberships_org_employee`, `account/app/models.py:317-324`, `postgresql_where=employee_id IS NOT NULL` |
| It was created in the initial schema migration and no later migration touches it | `5250ce5920bb_initial_account_schema.py`; the four later account migrations are indexes, RLS and role ownership |
| It is **read on every sign-in and every refresh** | `selectors.py:220` → `MemberOut` → `api/internal.py:108` → `auth/app/directory.py:152` → `auth/app/services.py:273` `subject_ref=membership.employee_id` → `auth/app/tokens.py:110-111` sets `sref`, **omitting the claim entirely when null** |
| The gateway maps `sref` → `X-Subject-Ref`, and `platform_core` turns that into `ctx.subject_ref` | `platform_core/gateway.py:347`; `platform_core/auth.py:238` |
| **Nothing writes it.** `account/app/services.py` never names the column. The only `Membership()` construction (`services.py:401`, in `add_member`, which `create_organization` and `accept_invitation` both go through) never sets it. `update_member`'s blind `setattr` loop (`services.py:488-490`) cannot reach it because `MemberPatch` (`schemas.py:166-173`) does not declare the field. Account's consumers bind product activation and the entitling subscription events and nothing else. No `/internal/v1` route writes anything. `tools/` never mentions it | `grep -rn employee_id services/account/app` returns six hits: the column, two lines of its own index, the selector read, the schema field on `MemberOut`, and the internal-directory read |
| The reverse half **is** writable, but **only at create**: `EmployeeIn.membership_id` exists, `EmployeePatch.membership_id` does not | `employee/app/schemas.py:90` vs `schemas.py:95-110` |
| The reverse half carries the mirror-image partial unique index | `uq_employees_org_membership`, `employee/app/models.py:131-138` |
| Nothing in the platform ever **sends** `membership_id` on employee create — not the HRMS SPA, not `tools/`, not the seeds. The HRMS client types it on `EmployeeOut` (a read) only | `frontend/product-hrms/src/api/types.ts:34`; no non-test occurrence in any form or request body |
| Employee's events carry `membership_id` on **`onboarded` only**. `_hierarchy_snapshot` — the full-state block on all three events — does not include it, so `updated` and `transitioned` do not | `employee/app/services.py:303` (explicit, on `onboarded`) vs `services.py:134-159` (`_hierarchy_snapshot`), used by all three |
| Account's `/internal/v1` router runs on `get_directory_session` — the `opsgrids_directory` role, **BYPASSRLS and SELECT-only**. It cannot write | `account/app/api/internal.py`, `SessionDep = Annotated[..., Depends(get_directory_session)]`; `tools/dev-bootstrap/init-db.sql` |
| Account's `/internal/v1` is guarded by a bare `require_service_token` — signature, audience, type, expiry, and nothing else. It has **no `iss` allowlist and no `org` claim check**; employee's has both | `account/app/api/internal.py` `dependencies=[Depends(require_service_token)]` vs `employee/app/api/internal.py:117-154` |
| The internal service token is a **shared HMAC secret**: any service that can verify one can mint one claiming any `iss` | `platform_core/internal_auth.py`; `employee/app/api/internal.py`'s module docstring says so at length |
| Each service connects as its own NOBYPASSRLS role confined to its own schema. **No role can read `employee.*` and write `account.*`** except `opsgrids_admin` (BYPASSRLS) | `tools/dev-bootstrap/init-db.sql` lines 28, 47, 83, 111 |
| `pv` is bumped **per organization**, not per membership, and a bump invalidates every access token in that tenant | `platform_core/cache.py:122-142`; ADR-0007 §5 |
| A refresh re-reads the directory, so a newly written link reaches the token on the next refresh with no cache in the way | `auth/app/services.py:539-563` (`rotate_refresh` → `directory.get` → `issue_for`); `auth/app/directory.py` has no cache |
| `access_token_ttl_seconds` is **900** | `platform_core/config.py:122` |
| `core.identity.membership.write` is already `is_dangerous=True`, defaults to ORG, ceilings at ORG, and every route that uses it is floored `minimum=Scope.ORG`. `ORG_OWNER` holds it via `CORE.all("ORG")` and `ORG_ADMIN` names it explicitly — so every tenant has holders | `account/app/catalogue.py:117-125`, `:189` (`ORG_OWNER`), `:202` (`ORG_ADMIN`); `account/app/api/routes.py:79-81` |
| **`is_dangerous` has no effect on request-time authorization.** `grep -rn is_dangerous libs/platform-core` returns **nothing**: the gate never consults it. It is catalogue metadata — declared in a service catalogue, written to `permissions` by `install_registration`, returned on `PermissionOut`. The one place it changes an outcome is *pricing*: `catalog`'s `is_read_only` excludes a dangerous permission from a view-only seat class | no hits in `libs/platform-core`; `account/app/services.py:1137` (write), `account/app/models.py:356`, `account/app/schemas.py:263`; `catalog/app/domain.py:169` (`action in READ_ACTIONS and not permission.is_dangerous`). **Added 2026-09-17**: this row is new, and it is the fact the Consequences originally got wrong |

So: **this column is the sole origin of `sref` for the entire estate, and no code path in the
platform can set it.** The link is writable today in exactly one direction — the direction nothing
reads — and only at the moment an employee record is created, which in a real onboarding is before
the person has a login at all.

The symptom is three-faced, and the worst face is the quiet one:

| Surface | What an unlinked principal gets |
|---|---|
| `GET /api/v1/approvals/inbox` | **HTTP 200, empty page, no explanation** — `approval/app/api/routes.py:224-225`, by ADR-0006 §6 rule 2's design |
| `POST /api/v1/approvals/delegations` | 403 `"No employee link to create a delegation"` — `routes.py:717-722` |
| Attendance check-in | `BusinessRuleProblem("Only an employee can punch their own attendance")` — `attendance/app/services.py:93-95` |

Two fail loudly. The one an approver reaches every morning fails silently, and looks exactly like
"you have no work today".

Two further facts frame the decision rather than following from it:

- **Both halves can already disagree**, and nothing reconciles them. Today they are almost certainly
  both null everywhere, which is why nobody has noticed.
- **`sref` is one slot serving ten products.** `customer` and `deal` read it as "the rep id"
  (`*/app/scope.py`), `timesheet` and `standup` as "the roster-person id", `itsm` as "the directory
  id", `metering` records it on usage events — while it is sourced from an HRMS employee id. That
  tension is older than this ADR and is not resolved here (see "Revisit when").

---

## Decision

### 1. `account.memberships.employee_id` is the authoritative link, and account is its only writer

One side is authoritative and the other is derived. Account wins, for one reason that outranks the
others:

**This is an authorization link, and identity is account's to assign.** `sref` decides what every
`OWN` scope in every product resolves to. ADR-0003 §3 puts that decision server-side at the
data-access boundary; ADR-0000 §1 puts the employee link on the membership precisely because "a
person can be an employee of one customer and a contractor for another". A product service must not
be able to change who a platform principal *is* — not by an event, not by an HTTP call, not at all.
Writing the link through `account/app/services.py`, in one tenant-bound transaction, with an audit
row, is the same chokepoint every other membership fact already goes through.

Three supporting reasons, each independently sufficient to break a tie:

- **It removes the propagation.** The value is written where it is read. There is no copy to order,
  no lag between "the admin linked them" and "the inbox works", and no relay that can stall and
  reproduce the exact silent failure this ADR exists to remove.
- **It survives a second people-provisioning product.** If employee were authoritative, a contractor
  or vendor system that also provisions principals would have to route identity through HRMS. Account
  is the platform; HRMS is a product a tenant may not own.
- **The dependency points the right way.** Products depend on the platform. Account consuming
  `hrms.*` would invert that for the most load-bearing column it has.

`employee.employees.membership_id` is therefore **not** authoritative for anything. §3 says what
happens to it.

### 2. The write is a first-class field on account's existing member API

`PATCH /api/v1/members/{membership_id}` — the endpoint that already exists, on the guard that already
exists.

- `MemberPatch` gains `employee_id: uuid.UUID | None`. `exclude_unset=True` already distinguishes
  "absent, leave alone" from "explicitly null, unlink", which is the distinction this field needs.
- **It is handled explicitly, before `update_member`'s `setattr` loop, and popped out of `changes`** —
  exactly as `location_codes` already is (`services.py:485-490`). The blind loop must never reach it:
  it would write the column with no conflict handling and no old-value audit, which is how a 23505
  becomes a 500.
- The audit `before`/`after` dicts (`services.py:478-483`) gain `employee_id`. An identity
  reassignment that is not in the audit trail is not defensible.
- **No new permission.** The guard stays `MemberWrite` — `require("core.identity.membership.write",
  minimum=Scope.ORG)` (`api/routes.py:79-81`). That permission is already ORG-ceilinged, already
  floored at ORG on every route that uses it, and already held by `ORG_OWNER` and `ORG_ADMIN` in
  every tenant. **Amended 2026-09-17 — this bullet also said the permission is `is_dangerous=True`
  and offered that as part of what makes the reuse safe.** The flag is true and buys nothing here:
  nothing in `libs/platform-core` reads it, so it changes no authorization outcome (Context table).
  What gates this write is the ORG floor and the grant itself.

**Why no dedicated `core.identity.membership.link` permission**, which is the obvious alternative and
would separate "edit a job title" from "reassign an identity": a new permission triggers ADR-0007's
full rollout — catalogue, export, deploy, reconcile — and if any step is missed, nobody in any
existing tenant can link anybody, which is this ADR's own failure mode wearing a different hat. The
population that would receive the new permission is byte-for-byte the population that already holds
`membership.write:ORG`. Nobody has asked for the separation. **The trade, stated plainly: anyone who
can edit a display name can reassign an identity.** Revisit when a customer asks for a member-admin
role that must not touch identity.

**Which SPA calls it is not an architectural question, and this ADR does not answer it.** It is worth
recording what the choice makes possible: `/api/v1/members` is a platform route with no `product` key
on `app-gateway`, and every product SPA holds a token on the same `app` audience — so the HRMS
employee page can call this endpoint with **the signed-in user's own token**, subject to that user's
own grants. The cross-product link needs no service token, no new internal surface and no new trust
relationship. That property is a large part of why this option wins.

### 3. Employee's half is frozen, and a write to it is refused loudly

`employee.employees.membership_id` stops being writable:

- `EmployeeIn` keeps the field and **refuses it**: supplying a non-null `membership_id` raises a
  `BusinessRuleProblem` naming `PATCH /api/v1/members/{membership_id}` as the place to do this.
  Removing the field instead would be worse — Pydantic's default `extra="ignore"` would swallow it,
  and an integration that has been setting it would keep "succeeding" while doing nothing. A field
  that refuses itself with a pointer is the loud option, and this ADR is about not being quiet.
- The column and `uq_employees_org_membership` are **kept**. Not for sentiment: the reconcile in §8
  reads the column as the estate's only record of anybody's intent, and the unique index is what
  guarantees that read is unambiguous — at most one employee can claim a membership, so the backfill
  never has to choose between two candidates.
- `EmployeeOut.membership_id` keeps returning it, documented as **advisory and possibly stale**.
  Account's `GET /api/v1/members` is the truth.

**No mirror is maintained, and no event is published.** The alternative — account publishes
`core.membership.employee_linked`, employee consumes it and keeps its column true — is one event
type, one consumer, one idempotency concern and one ordering concern, in exchange for a column that
**nothing in the platform reads**. `grep -rn membership_id services/employee/app` returns the model,
the two schemas and the event payload; no logic anywhere consults it. Every abstraction here has to
earn its place with two concrete current use cases, and this one has zero. ADR-0007 §5 publishes
`core.grants.changed` with no consumer, but that event already existed; *adding* a new event type
with no consumer is a contract we would be obliged to keep compatible forever in exchange for
nothing.

A later ADR drops the column once §8's reconcile has run across the fleet and reported a zero
population. That is a one-line migration, and it is cheaper to pay then than to guess now.

### 4. Account does not check that the employee exists, and that is deliberate

Account cannot read `employee.employees` — no cross-schema reads (ADR-0000 §10), and no database role
can reach both schemas (Context table). So existence validation means a synchronous HTTP call from
account to employee on an interactive write. Rejected:

- **It inverts the dependency on the request path.** ADR-0006 §1: neither side calls the other
  synchronously. A platform service's member-admin endpoint would start failing when a *product*
  service is down, and the degradation choice is a trap either way: refuse the link and an HRMS
  outage blocks member administration; accept it on failure and the validation is advisory, so a
  determined typo gets through during exactly the window nobody is watching.
- **It does not defend against the threat that matters.** The dangerous error is a link to the
  *wrong employee in the same tenant*, and that id exists. An existence check returns 200 for it.
- **A foreign-tenant id is inert, not a leak.** Every consumer of `sref` compares it inside a
  tenant-bound transaction under RLS — `approval.hierarchy_nodes` on `(organization_id, employee_id)`,
  `AttendanceRecord.employee_id` under `ctx.apply`, and so on. An employee id from another
  organization matches zero rows everywhere. It is junk, and junk is what §8's `--verify` pass is for.

**What actually defends the link**, in order: the partial unique index (§5), the audit row (§10), the
reconcile's `--verify` pass (§8), and the ORG-floored permission that gates the write at all — the
floor, not the `is_dangerous` flag beside it, which no gate reads (Context table).

Stated honestly: **a mistyped UUID is accepted and produces a principal whose `OWN` scope
silently resolves to nothing — the same symptom this ADR set out to remove, now reachable by
fat-fingering.** §8's verify pass is the only thing that finds it, and it is a command somebody has
to run. That is the one thing this decision makes worse, and it is recorded in Consequences rather
than smoothed over.

### 5. The partial unique index has an HTTP answer, not a 500

`uq_memberships_org_employee` fires when an employee already linked to one membership is linked to a
second. That must never surface as a raw 23505.

`update_member` does **both** of the following, and neither alone is sufficient:

1. **Pre-check.** A tenant-bound `SELECT` for a membership in this organization already holding that
   `employee_id`. If one exists and it is not this membership, raise `ConflictProblem` —
   HTTP 409, message *"That employee is already linked to another member of this organization"*, with
   the conflicting `membership_id` in the problem detail. The caller holds
   `membership.read`/`write` at ORG by construction, so naming the other membership reveals nothing
   they cannot already list, and it is the difference between an error and an instruction.
2. **Catch.** Wrap the flush and translate an `IntegrityError` whose constraint is
   `uq_memberships_org_employee` into the same `ConflictProblem`. The pre-check is a
   time-of-check/time-of-use race under concurrency; the catch is what makes it correct. The
   pre-check is what makes it legible.

**Nothing is auto-unlinked.** Resolving the conflict by silently clearing the other membership's link
is a re-assignment of identity that no one asked for, performed as a side effect of a PATCH. The
remedy is explicit: `PATCH` the holding membership with `employee_id: null`, then link the intended
one. Two audited operations, in that order.

**Revoking a member does not clear the link.** `revoke_member` sets `status = "removed"` and keeps the
row, and `add_member` reuses that same row when the same person returns (`services.py:397-401`) — so
auto-clearing would break the ordinary rehire and lose history for nothing. The consequence to know
about: the index does **not** exclude removed memberships, so a person who returns under a *different*
email gets a new membership whose link is refused by the index, with the old removed membership named
in the 409. That is the correct outcome — an administrator decides which record the employment
history belongs to — and rule 1's message is what makes it a thirty-second fix rather than a support
ticket.

**Amended 2026-09-17 — the reconcile obeys the same rule from the other side.** §8's backfill does
**not** fill a removed membership's link, for the reason this paragraph implies and did not draw out:
the link would resolve for nobody today (a removed membership mints no token) and would manufacture
the 409 above for a future rehire under a different email. So a removed membership's link only ever
changes by explicit human action — no machinery sets it, no machinery clears it.

Employee's index is untouched and, with §3's freeze, can no longer be violated by anything.

### 6. Ordering and idempotency: the copy is removed rather than ordered

The question the platform has answered before — employee before membership, or membership before
employee — **does not arise here**, and that is a property of the choice rather than a gap in it.

The link is written by a person, synchronously, to a row account owns, after both rows exist. An
administrator cannot link to a membership or an employee that does not yet exist; once both exist,
the link is one idempotent `PATCH`. Writing the same value twice is a no-op. There is no second
writer, no stream, and nothing to converge.

**ADR-0006 §2's `hierarchy_version` pattern does not apply, and the reason matters more than the
answer.** That version exists because approval holds an *asynchronous copy* fed by a stream whose
`occurred_at` is `outbox.created_at` — transaction *start* — so two commits can be stamped in the
order opposite to the one they landed in, and a naive "ignore older" rule would discard the newest
state forever. The version orders a copy. This ADR's design has no copy to order: it deletes the
second writer instead of sequencing it. A monotonic column and a `BEFORE UPDATE` trigger here would
be machinery guarding a race the design has removed.

The one place a second writer does exist is §8's reconcile, which runs concurrently with the
interactive path. Its rule is stronger than a version gate and needs no column: **it only ever fills
a null.** A row account has already linked is never overwritten, in either direction, by any run.

### 7. `pv` is bumped when a link is removed or changed, never when one is first set

`sref` rides in the access token (`tokens.py:110-111`), so a link written while somebody is signed in
does not reach them until their token is reissued. `rotate_refresh` re-reads the directory with no
cache (Context table), so a refresh always carries the current value. The only question is how
quickly a refresh is forced.

- **Null → an employee id: no bump.** The token catches up within one access-token lifetime — at most
  900 seconds. This is the routine onboarding path, and ADR-0000 §4 already names the asymmetry that
  decides it: a fifteen-minute window is tolerable for a *grant* and unacceptable for a *revocation*.
  It is also the path that must not be expensive: `pv` is bumped **per organization** (verified,
  `cache.py:113`), so bumping on every new hire would fire a full-tenant token-refresh burst — the
  cost ADR-0007 §5 paces a fleet reconcile to avoid — several times a day in a 4,000-person customer.

  Read this rule by the *previous value*, not by the word "re-link": a link **restored** after an
  unlink is null → an employee id like any other, so it does not bump. The remedy sequence in §12
  (clear the wrong holder, then link the right membership) therefore bumps exactly once, on the clear.
  Two engineers found this non-obvious in implementation and in the journey, so it is stated here
  rather than left to be re-derived from `previous_employee_id is not None`. The e2e suite pins it,
  and pins that the corrected person's own token stays `sref`-less — silently, with no error and no
  401 — until it is next minted. That silence is the same one §9 exists to surface.
- **A non-null value cleared or replaced: bump.** This is the revocation shape. It is the operation an
  administrator performs to *stop* somebody acting as an employee — most importantly to correct a
  wrong link — and fifteen more minutes of the wrong identity after the fix is not an acceptable
  answer. The bump costs that tenant one refresh burst; unlinking is rare enough that this is the
  right place to spend it.
- The bump goes **inside** the organization's transaction, and `bump_permission_version` raising on a
  Redis outage fails the request — the same reasoning and the same direction-to-be-wrong-in as
  `_invalidate_grants` and ADR-0007 §5.

**No mechanism here ends a session instantly.** A `pv` bump means the gateway refuses the stale access
token and the client refreshes into a correct one; a client that refuses to refresh gets 401s. For an
active-impersonation incident the lever that exists is `DELETE /api/v1/members/{id}`, after which
`rotate_refresh` finds no active membership and revokes the refresh family
(`auth/app/services.py:545-550`). A true immediate kill would be `auth.revoke_all_for_user`, which has
no API surface today. Flagged, not fixed.

### 8. Existing tenants: a reconcile, not an install — and the same pass verifies

Live rows exist with the column null. Following ADR-0007 §3's shape, discipline and vocabulary:

```
uv run python tools/reconcile_membership_links.py [--org <id> | --all] [--dry-run] [--pace 250]
```

**What it keys on.** `employee.employees.membership_id` where it is not null — the only recorded
statement of intent anywhere in the estate. Nothing else exists to key on.

**How it reads across the boundary.** A new endpoint on employee, beside the hierarchy one:

`GET /internal/v1/employee-links?organization_id=&cursor=&limit=` → `{items: [{employee_id,
membership_id}], next_cursor}`, keyset on `(organization_id, id)`, page size 500 capped at 1000 —
the same contract as `/internal/v1/hierarchy` (ADR-0006 §2.1). Every *failure* number there applies
unchanged: 10 s total timeout, 2 s connect, five attempts with 1·2·4·8·16 s full-jitter backoff on
connection errors, timeouts and 5xx only, a service token minted **per HTTP request** because
`service_token_ttl_seconds` is 60, 100 ms between pages.

**Amended 2026-09-17 — the one number that does *not* carry over is the transaction rule.** This
paragraph originally adopted ADR-0006 §2.1's table wholesale, which includes *"one transaction per
page on account's side"*, and then Write discipline below said *"one transaction per organization"*
and Audit said *"one row per organization per run, in the same transaction"*. Those three cannot all
hold, and the contradiction was this document's, not the engineer's. It is resolved in Write
discipline, in favour of one transaction per organization.

It returns **every** employee of the organization with its `membership_id` (null or not), not only
the linked ones, because one page serves both halves of the job:

- **fill** — for each row with a non-null `membership_id`, set that membership's `employee_id`;
- **verify** — every `employee_id` account holds that does not appear in the page is an id employee
  does not recognise. That is §4's mistyped UUID, and this is the only mechanism that finds it.

The page returns two UUIDs per row: no name, no employee number, no PII. That is deliberately *less*
than `/internal/v1/hierarchy` returns, so the bulk-PII exposure the earlier security pass flagged on
that endpoint does not exist on this one at all.

**It inherits employee's hardened internal posture, and tightens one screw.** `ALLOWED_INTERNAL_ISSUERS`
on the new endpoint is `{"reconcile-membership-links"}`, and the tool **binds the `org` claim**
(`mint_service_token(..., claims={"org": str(org_id)})`) — so this endpoint requires the claim from
day one rather than checking it only when present. That is precisely the gap
`employee/app/api/internal.py`'s docstring says `approval/app/employee_client.py` leaves open on the
hierarchy endpoint; the new caller closes it for the new endpoint rather than inheriting the hole.
The residual risk is unchanged and worth restating: the internal token is a shared HMAC secret, so
`iss` is a narrowing and not a cryptographic guarantee. Per-service keypairs or mTLS remain the
platform fix, out of this ADR's reach.

**Amended 2026-09-17 — how the allowlist is actually structured, because this paragraph read as one
flat constant.** Employee's internal router keeps `ALLOWED_INTERNAL_ISSUERS` as the **union** of every
route's callers and checks it at the router, then each route asserts its **own** narrower set
(`HIERARCHY_ISSUERS = {"approval"}`, `EMPLOYEE_LINKS_ISSUERS = {"reconcile-membership-links"}`). Both
checks are load-bearing and the reason is this ADR's: admitting the reconcile as a caller of the
*links* endpoint must not hand it the hierarchy endpoint's bulk-PII read as a side effect. Adopted as
the contract. The same change gives every successful internal read an audit row per page, naming the
organization, the cursor, the row count and the claimed `iss` — not specified here, better than what
was, and kept.

**Why not read both schemas from one process.** No database role can: each service has a NOBYPASSRLS
role confined to its own schema, and the only role that spans them is `opsgrids_admin` (BYPASSRLS).
Widening a bypass surface for a one-time job is the wrong trade (ADR-0002 §2) — the same trade
ADR-0006 §2 refused for the hierarchy backfill, refused again here, and for a *write* this time.

**Write discipline.** Account's app role, `session_scope(organization_id=…)`, **one transaction per
organization**, sequential with `--pace`, exactly as `tools/reconcile_permissions.py`.

**Amended 2026-09-17 — stated once, with the two alternatives rejected by name.** The order is:
**page employee-service to exhaustion first, hold the rows in memory, then open one transaction and
do every write for that organization inside it.** No database transaction is open while an HTTP call
is in flight, and none spans two organizations.

- **One transaction per page (what the inherited table said) is rejected.** It cannot produce this
  section's own audit row — one row per organization per run carrying that run's counters — because
  after the first page commits there is no longer a run to count; and it cannot honour `--dry-run`'s
  guarantee that a review run leaves nothing behind, because pages already committed are already
  committed. It also makes the "fill nulls only" rule weaker than it reads: a page boundary is a
  point at which a partially reconciled tenant is visible to live traffic.
- **Holding the transaction open across the HTTP paging is rejected, and it is the one a later
  reader would reach for** to avoid buffering. It puts a tenant-bound write transaction — and a
  pooled connection — at the mercy of a remote service for the full length of a paged walk: five
  attempts with 16 s backoff per page, times however many pages, against an `idle_in_transaction`
  budget and a connection pool sized for request traffic. A slow employee-service would then degrade
  *account's* write path, which is precisely the coupling ADR-0006 §1 exists to prevent, arrived at
  through a back door.
- **The cost of buffering is bounded and was measured against the shape, not guessed.** A row is two
  UUIDs (§8's page returns nothing else), so the largest realistic tenant — 50k employees — is a few
  megabytes, held for the length of one organization's run and released before the next. That is the
  price of the audit row and the dry-run guarantee, and it is worth paying. If a tenant ever makes it
  not worth paying, the answer is to buffer *less* — the fill half needs only the rows with a
  non-null `membership_id`, and the verify half needs only the set of employee ids — not to stretch a
  transaction over a network call.

**Fill nulls only.** A membership whose `employee_id` is already set is never overwritten — not to a
different value, not to the same one. This is what makes the command re-runnable, safe to run
concurrently with live traffic, and safe to interrupt. It is also what removes any need for §6's
version gate.

**Conflicts are reported, never resolved.** Per organization, and in the summary:

```
{org_id, links_written, conflicts_employee_taken, conflicts_disagree,
 unrecognised_employee_ids, unrecognised_membership_ids, memberships_unlinked,
 conflicts: [{kind, employee_id, membership_id, held_by_membership_id}]}
```

- `conflicts_employee_taken` — employee says E↔M, but a *different* membership already holds E.
- `conflicts_disagree` — employee says E↔M, but M already holds a *different* employee id.
- `unrecognised_employee_ids` — the verify half of §4.
- `unrecognised_membership_ids` — **added 2026-09-17.** Employee names a `membership_id` account will
  not fill: either account has no such membership, or the membership has been revoked. See below.
- `memberships_unlinked` — active memberships still with no link, the number an operator can act on.
  Deliberately the same shape as ADR-0007 §4's `memberships_without`, for the same reason: an
  automatic fix to a disagreement about who somebody *is* is not a fix.
- `conflicts` — **added 2026-09-17.** The ids behind the two conflict counters, in the per-organization
  JSON. "Three memberships disagree" is a statistic; "*these* three" is a work item, and the remedy in
  §5 is a `PATCH` that needs the ids to be typed into it. Every id in the array is a membership or
  employee UUID in the organization the operator is already reconciling, so it discloses nothing the
  same operator cannot list through `GET /api/v1/members`. Counters stay in the audit row; the array
  is report output.

**Amended 2026-09-17 — two things this section did not say, one of which the implementation had to
decide unaided.**

**A membership id employee names but account will not fill is counted, not skipped.** The original
report shape had nowhere to put it: it is neither conflict (account holds no competing value) nor an
unrecognised *employee* id (that counter runs the other way, over ids account holds). Silently
dropping the row hides a real disagreement between the two schemas — exactly the class of silence
this ADR exists to remove — so `unrecognised_membership_ids` carries the ids.

**The reconcile does not fill a *removed* membership's link.** Account has the row; it declines to
write it, and reports the id. The reasoning, and it is the same reasoning §5 already applies from the
other direction:

- Filling it buys nothing *now*. A removed membership mints no token — `rotate_refresh` finds no
  active membership and revokes the family (§7) — so the link it would carry resolves for nobody.
- It can cost something *later*. §5 records that the partial unique index does **not** exclude removed
  memberships, so a person who returns under a different email gets a new membership whose link the
  index refuses, with the old removed membership named in the 409. Filling a removed row manufactures
  exactly that obstruction, in bulk, for people who have left.
- The one case where filling would have helped — a rehire under the *same* email, where `add_member`
  reuses the same membership row (§5) — needs no help from this command: the row is reactivated with
  its facts intact, and if the link is absent it is one interactive `PATCH`, by a human who is looking
  at that person anyway.

**This is consistent with §5, and the pair should be read together.** Neither path ever writes or
clears the link on a removed membership: the interactive path refuses to auto-clear one a human
created, and the reconcile refuses to create one no human asked for. The invariant is that a removed
membership's link only ever changes by explicit human action.

**Known limitation, and the trigger to fix it.** `unrecognised_membership_ids` merges two causes with
different remedies — "account has no such membership" (a genuine disagreement, possibly a stale
pointer from another environment) and "the membership was revoked" (an ordinary, boring state). On
this repository both populations are expected to be empty, so one counter is the cheaper contract. If
a real run ever reports a non-zero count, **split it into two before anybody acts on the output**;
mixing a routine state into a report of disagreements is how a report gets ignored.

**`pv` is never bumped by this command.** It only fills nulls, so every write it makes is
grant-shaped, and §7 says grant-shaped changes wait for the token. This also keeps a fleet run from
being a fleet-wide refresh storm.

**Audit.** One row per organization per run, in that organization's one transaction (Write
discipline): `action = "membership.links_reconciled"`, `entity = "organization"`, `actor_id = NULL`,
`acting_as = "reconcile-membership-links-cli"`, `after` carrying the counters above — including
`unrecognised_membership_ids`. An empty diff writes no audit row. The `conflicts` array is report
output only: the audit row records what this run *did*, and a conflict is precisely what it declined
to do.

**`--dry-run` is the expected first invocation**, and the expected result is **zero**. Nothing in
`frontend/`, `tools/` or the seeds has ever sent `membership_id` on employee create (Context table),
so `links_written: 0` is the predicted outcome and a non-zero one is itself the finding: somebody
outside this repository was using the field.

**No scheduled job is created.** ADR-0000 §10 calls for a nightly orphan check on cross-schema UUIDs
and the platform has never implemented one; this ADR gives that check a concrete implementation for
one link and stops there. Scheduling it needs an owner and a destination for the alert, and inventing
either would be inventing operations this loop has not designed.

### 9. Surfacing the unlinked state — and what deliberately stays quiet

**The approval inbox keeps returning HTTP 200 and an empty page. ADR-0006 §6 is not amended.** Three
reasons, and the first is decisive:

- **An error there would be an unreliable signal.** An *exited* employee also gets an empty inbox, by
  ADR-0006 §6's slice-2 amendment, with a perfectly good `sref`. Making "unlinked" loud and "exited"
  quiet would split one state — "you may decide nothing" — across two response codes on evidence the
  endpoint does not have.
- A 4xx on a list endpoint a SPA polls converts a provisioning gap into a client error loop.
- The inbox is the *symptom*. The diagnosis belongs where the fix is.

What is added instead, using only surfaces that already exist. **Amended 2026-09-17 — each item now
names the service that owns it.** The original list read as one deliverable; it is three, in three
ownership boundaries, and the third fell outside both implementing engineers' — which is how a listed
item nearly became an unlisted gap:

1. **`GET /api/v1/members?linked=false`** — a filter on the existing list endpoint, under the existing
   `core.identity.membership.read` guard, narrowed by the scope that endpoint already applies. It is
   the same screen an administrator uses to fix the problem, which is the point. No new endpoint, no
   new permission, no screen this loop has not designed — a filter a screen can adopt when one is.
   **Owner: `services/account`. Delivered 2026-09-17** with this ADR's account changes.
2. **The reconcile's `memberships_unlinked` count** (§8), for the fleet view.
   **Owner: `tools/reconcile_membership_links.py` + `services/account`. Delivered 2026-09-17.**
3. **One structured log line in approval** when the inbox is served to a caller with no `subject_ref`
   — `approval.inbox.no_subject_ref` with the organization and membership — at `info`. Bounded by how
   often unlinked people open an inbox, and it makes "my approvals page is empty" answerable from logs
   without a database session. This is the least valuable of the three and is listed last on purpose.
   **Owner: `services/approval` — not account, not employee, and not this ADR's two implementing
   changes.** It touches `approval/app/api/routes.py`'s existing no-`subject_ref` branch and nothing
   else. **Delivered 2026-09-17** as a separate change.

   Two things about the delivered form are worth keeping. It is emitted at **one** site, not four:
   the three other null-`subject_ref` paths in that service are `create_delegation` and `get_chain`,
   which already refuse loudly, and `requests_stmt`'s `mine_only` branch, which fires only when the
   client sends `?requester=me` and would put a second line on the same screen's other request. And
   the tests pin the distinction this section's first bullet rests on: an **exited** linked caller
   gets the same empty page and does **not** emit the line, so the two empty-inbox states stay
   distinguishable in logs rather than collapsing into one signal.

   If it is ever dropped rather than kept, drop it here in writing — an unimplemented item in a
   delivered ADR is the same class of silence as the empty inbox.

Unchanged: the delegation 403 and attendance's business-rule error both already say the right thing.

**Rejected as surfacing mechanisms:** turning the empty inbox into an error (above); a health check
that fails on unlinked members (a provisioning state is not a service fault, and it would page
somebody about a customer's data entry); a notification (`services/notification` is a 171-line
scaffold with no delivery — ADR-0006 Context); a dedicated `/unlinked` endpoint (a filter on the list
that already exists costs one query parameter); and a banner or empty-state copy, which is a screen
decision this ADR has no standing to make.

### 10. Audit

Every link write goes through `record_audit` in the same transaction, on the existing
`membership.updated` action, with `employee_id` added to `before` and `after` (§2). No separate action
code: an auditor reconstructing "who decided this person was that employee" reads one membership's
trail in one vocabulary, and splitting identity assignment into its own action would hide it from
anyone reading the member's history rather than searching for it.

The reconcile writes its own organization-level row (§8). Employee's refused write (§3) writes
nothing — a rejected request is not a change.

---

## Blast radius

**Every service that reads `sref` changes behaviour when a link is written**, because for the
affected principal `OWN` scope goes from matching nothing to matching rows:

| Service | What `sref` means there |
|---|---|
| `platform_core` | `ctx.subject_ref`, `ctx.apply(owner_col=…)`, `assert_can_touch` — the enforcement point for every `OWN` rung on the platform (`auth.py:238`) |
| `auth` | mints the claim (`services.py:273`) |
| `approval` | inbox, `/requests` at OWN and TEAM, `/chain/me`, decide eligibility (ADR-0006 §6 conditions 2–4), delegation creation and `created_by_employee_id`, `decided_by_employee_id` |
| `attendance` | `_subject_employee` for every punch, OWN scope on records, regularisation requests |
| `employee` | `hrms.people.employee.read:OWN` — "my profile", and the unmask path on one's own PII |
| `customer`, `deal` | CRM's OWN rung — "the deals a rep owns" (`*/app/scope.py`) |
| `itsm` | "the incidents a caller raised"; `requester_id=ctx.principal.subject_ref` (`services.py:209`) |
| `project`, `projects`, `standup`, `timesheet` | OWN over assignments, check-ins and weeks |
| `metering` | records `subject_ref` on usage events (`models.py:94`) — so a wrong link ends up in billing-adjacent data |

Two consequences that are easy to miss:

- **Linking a membership switches on OWN scope in every activated product, not only HRMS.** The link
  is written on an HRMS-shaped screen and lands on a CRM rep's deal list.
- **An organization with no HRMS has no employee records, so no membership can be linked, so every
  OWN rung in CRM, ITSM, projects and timesheets is permanently empty for that tenant.** This ADR
  makes the link *writable*; it does not make it *populatable* without HRMS. See "Revisit when".

**A wrong link is an authorization defect, not a typo.** Concretely, if membership M is linked to
employee E when it should have been employee F:

- M's holder punches attendance **as E**, and edits E's records.
- M's holder sees E's approval inbox and can decide E's requests; `decided_by_employee_id` records
  **E**, so the audit trail names a human who did not make the decision. That is an audit-integrity
  failure on top of an authorization one.
- M's holder reads E's employee record at `read:OWN` and, holding the unmask permission, can unmask
  E's PII — with an audit row that correctly names the reader and silently misattributes the subject.
- Because of the partial unique index, the wrong link also **displaces** F: no membership can be
  linked to E while M holds it, so the real E loses access to their own data until an administrator
  unlinks M. The conflict message naming the holding membership (§5) exists for exactly this.

The incident response is: `PATCH` the wrong membership with `employee_id: null` (which bumps `pv` per
§7, so the impersonating token dies at the gateway within seconds), then link correctly, then read the
`membership.updated` audit rows to bound the exposure window.

---

## What this rejects

- **Event-driven: employee publishes, account consumes and sets the column.** Loses on three counts.
  *Authority:* it lets a product service decide who a platform principal is — the objection that
  outranks all the mechanics (§1). *Cost:* the events do not carry what is needed —
  `_hierarchy_snapshot` omits `membership_id`, so only `onboarded` has it
  (`employee/app/services.py:303`), and `EmployeePatch` has no `membership_id`, so the link cannot
  change after create at all. Making this work means adding the field to the shared snapshot,
  adding it to `EmployeePatch`, and adding an account consumer bound to `hrms.*` — the first time
  the platform's identity service would depend on a product's event contract. *Coverage:* it serves
  only "employee created after the membership, with the id supplied at create", which is the rarer
  order — HR imports employees before those people have logins. The common order is unserved.
- **An internal endpoint on account, called by employee.** Loses hardest, and would have been the
  most tempting. Account's `/internal/v1` runs on `get_directory_session` — BYPASSRLS and
  **SELECT-only** — so a write endpoint cannot live there without either a second session dependency
  inside a router whose every other route is a cross-tenant bypass read, or a second router. Worse,
  that router is guarded by a bare `require_service_token` with no `iss` allowlist and no `org` claim,
  over a **shared HMAC secret**, with a caller-supplied `organization_id`. The earlier security pass
  flagged the analogous shape on employee as a bulk-PII read risk; the same shape on a *write to
  `employee_id`* is not a read risk, it is an impersonation primitive — any holder of the shared
  secret could assign any employee identity to any membership in any tenant. No amount of `iss`
  allowlisting fixes that while the secret is shared. It also puts a synchronous product→platform
  call on a write path ADR-0006 §1 says must not exist.
- **Employee as the authoritative side with account mirroring.** The mirror image of §1, and it fails
  the same test: identity would be assigned by a product, and the value `sref` reads would arrive over
  a relay that can stall — reproducing the silent empty inbox with more moving parts.
- **Maintaining both halves with a reconciler.** Two writers and a tie-breaker, for a single UUID.
  This is the shape ADR-0006 §2 refused for `manager_membership_id` ("a second, unsynchronised
  manager field") and declined to repeat.
- **A dedicated `core.identity.membership.link` permission** (§2).
- **Synchronous existence validation from account to employee** (§4).
- **Auto-unlinking the conflicting membership**, and **auto-clearing the link on member revocation**
  (§5).
- **A `link_version` column and trigger**, copying ADR-0006 §2's `hierarchy_version`. It orders a copy;
  this design has no copy (§6).
- **A new `core.membership.employee_linked` event**, and a mirror consumer in employee (§3).
- **Turning the empty approval inbox into an error** (§9), and every other surfacing mechanism listed
  there.
- **A two-schema reconcile running as `opsgrids_admin`** (§8).
- **A transaction per page in the reconcile, and a transaction held open across its HTTP paging**
  (§8, Write discipline — added 2026-09-17). The first cannot produce one audit row per run or honour
  `--dry-run`; the second couples account's write path to employee-service's latency.
- **Filling a removed membership's link during the reconcile** (§8, and §5 from the other side —
  added 2026-09-17).

## Consequences

- **The link becomes writable, by one path, through the chokepoint that already audits every other
  membership fact.** Approval's inbox, attendance check-in, delegation, and OWN scope in every other
  product start working for a linked principal.
- **This ADR ships without a permission rollout.** It adds no permission, so ADR-0007's
  export → deploy → reconcile sequence does not apply and there is no tenant in which the new
  capability lands unheld. That is a direct consequence of reusing `membership.write` and is most of
  why §2 chose it.
- **A mistyped employee id is accepted and fails silently** — the same class of symptom this ADR
  exists to remove, now reachable by mistake rather than by omission. §8's `--verify` pass is the
  only detector, and it is a command somebody has to run. This is the honest cost of refusing a
  synchronous cross-service validation (§4), and it is the first thing to revisit if it ever bites.
- **Identity assignment rides on `membership.write:ORG`.** Anyone who can edit a display name can
  reassign an identity. **Amended 2026-09-17 — there are two mitigations, not three.** This bullet
  said the risk was *"mitigated by `is_dangerous=True`, the ORG floor and the audit row"*. The first
  of those is not a mitigation: `is_dangerous` has **zero references in `libs/platform-core`**, so no
  authorization gate consults it and it does nothing for this write. It is catalogue metadata —
  written by `install_registration`, stored on `permissions`, exposed on `PermissionOut`. It is not
  inert *everywhere*: `catalog/app/domain.py:169` reads it to keep a dangerous permission out of a
  view-only seat class, which is a pricing decision made at entitlement time, not a check made at
  request time. So what actually mitigates this is **the ORG floor on every route that uses the
  permission, and the audit row on every write** (§10). Neither eliminates it.
- **The reconcile buffers one organization's employee rows in memory** (§8, added 2026-09-17) — two
  UUIDs per row, a few megabytes at 50k employees. That is the price of one audit row per run and of
  `--dry-run` rolling back as a unit, and it is bought deliberately rather than by omission.
- **Unlinking is a tenant-wide token-refresh event** (§7). Rare by construction, but it is not a
  silent operation and should not be done casually in a 4,000-person tenant during business hours.
- **`employee.employees.membership_id` becomes a column nothing writes and nothing reads**, still
  published on `EmployeeOut` and typed in the HRMS client. That is a seam to watch: a column that
  looks settable and is not, until a later ADR drops it. §3's explicit refusal, with a pointer to the
  right endpoint, is what keeps it from being a trap rather than a leftover.
- **Employee gains a second internal endpoint**, and therefore a second internal surface to keep
  hardened. It returns strictly less than the first one (two UUIDs, no PII) and it is the first caller
  in the platform to bind the `org` claim — but it is still one more door.
- **A CRM-only or ITSM-only tenant gains nothing.** With no HRMS there are no employee records, so no
  membership can be linked and every OWN rung in those products stays empty. This ADR makes that
  visible rather than fixing it. Note the unpleasant workaround it also makes possible: because
  account does not validate (§4), such a tenant *could* write whatever id CRM stores in
  `Deal.owner_id` into `employee_id` and OWN would start working — an abuse the column's name invites
  nobody to discover, and an argument that the column is really a generic subject reference.
- **`sref` stays one slot for ten products.** `customer`/`deal` read it as a rep id, `timesheet`/
  `standup` as a roster-person id, `itsm` as a directory id, while it is sourced from an HRMS employee
  id. This ADR entrenches that by naming account's column authoritative under its current name. The
  alternative — renaming it `subject_ref` now — would imply a generality the token does not have and
  cost a migration plus a public schema change for no behaviour. Deferred deliberately.

## Revisit when

- **A second product provisions principals** (contractors, vendors, a partner portal). §1's reasoning
  holds — account stays the writer — but "which product's identity is `sref`" becomes a real question,
  and the answer is probably a per-product subject reference in the token. That is a change to
  ADR-0000 §3's token shape and §6's resolver contract, and it needs a second product asking, not an
  imagined one.
- **A tenant without HRMS needs `OWN` scope to work.** The same trigger from the other end. Until
  then, the workaround above is a documented abuse, not a supported path.
- **A wrong link causes a real incident.** That is the evidence §4 lacks. The cheapest response is
  not synchronous validation but making §8's verify pass scheduled, with an owner and a destination
  for its output — which also closes ADR-0000 §10's long-outstanding nightly orphan check for this
  link.
- **A customer asks for a member-admin role that must not reassign identity.** Then §2's reuse of
  `membership.write` becomes a dedicated permission, with ADR-0007's full rollout.
- **Anything starts reading `employee.employees.membership_id`.** Then §3's freeze becomes a
  maintained mirror, with an event, a consumer and — because the mirror *would* be an asynchronous
  copy — ADR-0006 §2's version gate after all. Until then the column is dropped by a later ADR once
  §8 reports a zero population fleet-wide.
- **Per-service signing keys or mTLS land in `libs/platform-core`.** Then §8's `iss` allowlist stops
  being a narrowing and becomes a guarantee, and account's own `/internal/v1` — still guarded by a
  bare `require_service_token` — should be brought up to the same posture rather than left as the
  weaker of the two.
- **A real run reports a non-zero `unrecognised_membership_ids`** (added 2026-09-17). Then that
  counter splits into "account has no such membership" and "the membership was revoked", because the
  remedies differ and only the first is a disagreement. Until then one counter is the cheaper
  contract (§8).
- **`is_dangerous` acquires a request-time meaning** (added 2026-09-17). If `libs/platform-core` ever
  makes the gate consult it — a step-up check, a re-authentication prompt, a stricter audit path —
  then §2's reuse of `membership.write` gains the third mitigation this ADR wrongly claimed it
  already had, and both §2 and the Consequences should be re-read in that light. See ADR-0000 §5's
  note.
- **`pv` becomes per-membership rather than per-organization.** §7's asymmetry exists entirely because
  a bump is tenant-wide; a per-membership version would let the first link bump too, and the
  fifteen-minute wait would disappear.

---

## Decisions on open questions (2026-09-24)

Five things this ADR left open are decided here. Its other "Revisit when" items are genuine demand
triggers and stay as they are. The register of every ADR question is
[open-questions-register.md](open-questions-register.md).

### Q1. Which screen writes the link (§2) — **DECIDED: the HRMS employee page, with the signed-in user's own token**

**Decision.** The employee detail page in `frontend/product-hrms` gets a **"Linked member"** field:
- the picker is `GET /api/v1/members?linked=false` (§9 item 1, already delivered);
- linking calls `PATCH /api/v1/members/{membership_id}` with `{employee_id}`;
- unlinking is an explicit action whose confirmation says it refreshes everyone's sign-in in the
  organization (§7's bump).

The control renders only when `GET /api/v1/members/me/grants` resolves
`core.identity.membership.write:ORG`. That is a UI hint; the server's ORG floor is the control.
The dashboard's Members screen stays the one that never sends `employee_id`
(`frontend/opsgrids-dashboard/src/api/types.ts`), so there is one writing screen, not two.

**Why.** Today nothing in `frontend/` sends `employee_id`, so the link §1 made writable can only be
set with curl, and every OWN rung this ADR exists to switch on stays empty in practice. HR is the
person who knows which employee record a new joiner is, at onboarding. §2 already showed the HRMS
SPA can call this platform route with the user's own token.

**Rejected.** A screen in the dashboard's Members admin. Members is where an org admin manages
access, but choosing an employee record there needs a read of employee data, which belongs to
HRMS. An automatic link by matching emails: ADR-0000 §1 forbids identity by email, and §4 explains
why a wrong link is an authorization defect.

**Consequences.** It is one field, one picker and two calls. **Not covered by any ledger item. New
backlog item needed.** It is the delivery that makes this ADR usable without an API client.

### Q2. An immediate, user-level kill (§7: "`auth.revoke_all_for_user` has no API surface") — **DECIDED: an operator route**

**Decision.** Within a tenant, the lever already exists and is immediate. `DELETE
/api/v1/members/{id}` bumps `pv` (`revoke_member`) and the next refresh fails. Across tenants —
a compromised credential — the kill is an **operator** route on admin-gateway:
`POST /api/v1/auth/operator/users/{user_id}/sessions:revoke`. Details:
- it is guarded by `require_operator({"owner", "support"})` (ADR-0003's operator matrix) and needs
  a written reason;
- it calls `auth.services.revoke_all_for_user`;
- it bumps `pv` in every organization where the user holds an active membership, so outstanding
  access tokens die at the gateway rather than living out their 900 seconds;
- it writes an audit row naming the operator, the user and the reason.

**Why.** An incident needs this, and the function already exists. A customer admin must not be
able to end a user's sessions in *other* customers' tenants, so the cross-tenant form belongs only
on the operator surface.

**Rejected.** A tenant-admin route that revokes all of a user's sessions: that reaches across
tenants. Letting tokens expire: fifteen minutes of a compromised account is the window ADR-0000
§4 exists to close.

**Consequences.** Every organization the user belongs to pays one token-refresh burst, which is
acceptable for an incident. **Not covered by any ledger item. New backlog item needed.** It belongs
beside Q1's staff console backend and after it merges.

### Q3. Scheduling the verify pass (§8: "No scheduled job is created") — **DECIDED: nightly, report-only, alerting through ops-monitor**

**Decision.** The verify half of `tools/reconcile_membership_links.py` runs **nightly in
`--dry-run` mode** as one check in the nightly orphan-check job that ADR-0000 §10 always called
for. A non-zero `unrecognised_employee_ids`, `unrecognised_membership_ids` or any conflict counter
raises an alert through ops-monitor (C10) to `OPS_ALERT_EMAIL`. The destination and owner are the
platform on-call, which is the owner this section said was missing. The fill half is never
scheduled. Filling stays a reviewed, human-run command.

**Why.** §4 names this pass as "the only thing that finds" a mistyped link, and a check that
somebody has to remember to run does not get run.

**Consequences.** **Implemented by R21** (schedule the link-verify pass), together with **R13**
(the nightly orphan check it joins) and **R7/C10** (the alert route).

### Q4. When `employee.employees.membership_id` is dropped (§3) — **DECIDED: after one production dry-run reports zero**

**Decision.** The column and `uq_employees_org_membership` are dropped by R21's migration once
**the owner has run `reconcile_membership_links.py --all --dry-run` on production** and it reports
`links_written: 0` with no conflicts. That report is evidence that nothing outside this repository
used the field. The same change removes `EmployeeOut.membership_id` and its HRMS client type.
`EmployeeIn.membership_id` **keeps refusing** permanently, with its pointer to
`PATCH /api/v1/members/{id}`. A removed field would be silently ignored under Pydantic's
`extra="ignore"`, which is the silence §3 rejected. After the drop, the links endpoint returns
employee ids only, so the verify half keeps working.

**Consequences.** **Implemented by R21. Owner input needed:** the production dry-run output.

### Q5. `sref` as one slot for ten products, and OWN scope in a tenant without HRMS — **DECIDED (direction): HR-shaped products keep the employee link; every other product keys OWN on the membership**

**Decision.** Which subject the OWN rung compares against is a **per-product resolver choice**, the
indirection ADR-0000 §6 kept for exactly this:
- **HR-shaped products** — `hrms` (employee, attendance, leave, payroll, recruit) and approval's
  routing — keep OWN on the employee link (`sref`), as built.
- **Every other product** — CRM (customer, deal, lead), ITSM, projects/timesheet/standup — keys OWN
  on the **membership** (`mem` claim → `ctx.principal.membership_id`). Every signed-in principal
  has one in every tenant, whether or not the tenant owns HRMS.

The platform-core change is small: a `ScopeResolver` may declare that its subject is the
membership, and `ScopeContext.apply` / `assert_can_touch` compare that product's owner column
against it. **No token change**: `mem` is already a claim (ADR-0000 §3 untouched). Each product
switches in its own rebuild slice, and existing owner values are migrated from employee ids to
membership ids through account's link, reported rather than guessed for rows that cannot be
mapped (§8's discipline). Metering records the membership id beside `subject_ref` from the same
change.

**Amended 2026-10-07 — two statements in this decision do not match the code or the data.**
(1) *The migration recipe.* CRM owner values are not employee ids that can be carried across
"through account's link". They are `customer.owners.id`, a uuid the model generates and the public
route cannot choose (`services/customer/app/schemas.py:50-58`), except on deals and activities,
which default `owner_id` to the caller's `subject_ref`
(`services/deal/app/api/routes.py:112,241-243`), so a CRM organization holds a mix of roster ids and
employee ids. The only evidence that ties a roster row to a person is its email, and Q1 above
rejected email matching for the HRMS link, so a CRM mapping would be a human-reviewed plan, never an
automatic rewrite. [ADR-0038](0038-crm-programme-decisions.md) proposes that plan and a
`membership_id` column on the roster rather than a rekey (pending D-7, D-8). (2) *"The platform-core
change is small."* `ScopeContext.apply` and `assert_can_touch` compare against
`principal.subject_ref` only (`libs/platform-core/src/platform_core/auth.py:341,373`), and
`register_scope_resolver` writes a dictionary that nothing reads
(`libs/platform-core/src/platform_core/auth.py:297-301`), so a resolver cannot declare a membership
subject today and the work is larger than stated here.

**Why.** It has already happened. CRM is sold standalone (ADR-0013), and CRM's resolvers
(`services/deal/app/scope.py`, `services/customer/app/scope.py`, and `lead` in ADR-0017) compare
`owner_id` with `subject_ref`. So in a CRM-only tenant every OWN-scoped rep sees nothing, the
"Consequences" bullet above. The documented workaround — writing a CRM roster id into
`employee_id` — is an abuse of an authorization link. The membership is per-organization (ADR-0000
§1), survives a rehire (`add_member` reuses the row, §5), and needs no HRMS.

**Rejected.** A per-product subject reference minted into the token: a token-shape change for a
value the gateway already forwards (`mem`). Falling back to the membership only when no employee
link exists: a person's OWN rows would change the moment HR links them. Keeping the status quo:
OWN would stay permanently empty for most tenants of four products.

**Consequences.** CRM's OWN semantics change in C15, for all three `crm` processes at once,
including the `lead` scope that ADR-0017 ships on `subject_ref`. It changes for projects,
timesheet and standup in the PulseBoard rebuild (C14/Q11), and for ITSM in its own slice. The
platform-core resolver change and the ITSM change are **not covered by any ledger item. New
backlog items needed.** C15 and C14 must name this in their scope.

## Amendment 2026-10-15: the screens that write the link, and why signup links nobody

A real self-serve customer signed up, started trials and found that the owner's login had no
employee identity and no way to give itself one: HRMS Attendance said "No employee record is linked
to your account" and Leave said "Ask an administrator to link...", and the administrator was the
person reading it. Q1 above decided *which screen* writes the link (the HRMS employee page) but
nothing had built it, so the link remained curl-only. This amendment records what was built and the
one decision it turned on.

**Built (all on the existing `PATCH /api/v1/members/{membership_id}`, no new permission, no new
service-to-service call, no change to who writes the column):**

- **Employee page -> "Linked member"** (`frontend/product-hrms/src/ui/LinkedMemberCard.tsx`).
  Renders only for a caller holding `core.identity.membership.write:ORG` (a hint; the server's ORG
  floor is the control). Shows the login linked to this employee, or a picker of `?linked=false`
  members to link one; **Unlink** is a confirmation that says it refreshes everyone's sign-in (§7).
  To answer "who is linked to *this* employee" without paging the directory, `GET /members` gained
  an `employee_id` filter -- a filter inside the scope `members_stmt` already applies, like
  `linked`, so it narrows and never widens (`services/account/tests/test_member_employee_filter.py`).
- **"Create my employee record"** where the empty states used to dead-end (Attendance, Leave;
  `LinkMyLoginCard.tsx`). For a caller who may both write memberships and write employees at ORG: it
  creates an employee named as the login is (`employee_no` is now optional on `POST /employees`: the
  next free `EMP####` is generated under a per-organization advisory lock), then links the login
  through account, then refreshes the session so the new `sref` reaches the page. The two writes are
  not one transaction -- they are two services, and account is the link's only writer (§1) -- so a
  failed link keeps the created employee in the component and the retry reuses it; a page left in
  between leaves a normal employee record that is linked from its page.
  Someone who may link but not create is sent to Employees; everyone else is told whom to ask.

**Decided: the signup does not create or link an employee, for the owner or anyone.** The tempting
fix was for `signup` to mint an employee for the new owner. It is rejected, for the reasons this ADR
already holds: (1) at signup no product is active, so there is no employee service entitled to hold
the record and nothing to write it to; the trial starts later, by a different event; (2) the link is
an authorization fact (§1) and a product service must not be able to write it, so the write would
have to come from account to employee synchronously -- the cross-service call §4 refuses on an
interactive write -- or as an event consumer in employee, which would be a product service deciding
who a platform principal *is*; (3) a tenant that buys CRM alone has no employee service at all, and
inventing an HR record for it would be wrong data. What replaces it is that the owner can do it in
one tap, when and where they need it (above), and that CRM no longer needs the link at all for the
owner to work (ADR-0038, "Amended 2026-10-15").

Nothing here changes §7: a link set for the first time takes effect at the login's next token refresh
(the card asks for one); unlinking or changing one bumps `pv` as before.
