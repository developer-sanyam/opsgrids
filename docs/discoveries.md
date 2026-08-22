# Discoveries

Defects and surprises found in this codebase, what caused them, and what would have caught them.
Kept because the *pattern* behind each one is more useful than the fix.

---

## Every one of these passed its own test suite

That is the thread running through this page, and it is worth stating before the list. Each
defect below sat behind green tests. Three of them were only visible when two components ran
together, and the suite that would run them together **still does not exist**. See
[testing.md](testing.md).

---

## 1. account-service served none of the internal endpoints

**Impact: total. The platform could not serve one real request.**

auth-service called `/internal/v1/users/by-email` to resolve sign-in. Both gateways called
`/internal/v1/memberships/{id}/grants` on every authenticated request. account-service
implemented **none of them**.

In a real deployment nobody could sign in, and the gateway failed closed with `503` on every
authenticated request.

**Why it survived:** auth's tests used a fake `Directory`; the gateway's tests stubbed the
permission resolver. Each side tested against its own idea of the other. Both suites were green.

**Caught by:** noticing during a code read that the consumer paths had no matching producer.
Nothing automated found it.

## 2. Revocation silently did nothing

**Impact: a revoked role kept working until the access token expired.**

`bump_permission_version` existed. The gateway checked the `pv` claim on every request.
`set_member_roles` said **in its own docstring** that it bumped the version. Nothing called it,
so the counter never moved and the staleness check was unreachable code.

**Why it survived:** the gateway's tests set the counter by hand to test the check. The
account-service tests never asserted the counter changed. The contract between them was
untested — and the docstring made it read as done.

**Lesson:** a docstring describing behaviour is not evidence of it. Where two components agree on
a counter, assert the counter.

## 3. The gateway duplicated the correlation header

**Impact: every cross-service trace keyed on a doubled value.**

Header names are case-insensitive; a Python dict is not. The client's `x-correlation-id` and the
gateway's `X-Correlation-ID` survived as two entries, and downstream received
`trace-me, trace-me`.

**Fix:** lower-case every forwarded header key, so writing one always *replaces* the caller's
value rather than appending to it.

## 4. `staff_only` was parsed and never enforced

**Impact: cross-customer admin endpoints reachable by any operator-audience token.**

`routes.json` marked the admin routes `staff_only`. The gateway checked a **gateway-wide** policy
flag that `admin-gateway` never set, so the per-route flag was decorative.

The flag has to be per route, because that gateway also carries service-to-service routes and
service calls hold no staff claim — a gateway-wide flag would either break those or leave the
staff routes open.

**Caught by:** writing the admin-gateway test suite, which had not existed.

## 5. Row-level security is inert for three independent reasons

**Impact: tenant isolation rests entirely on query discipline.**

Not one gap but three, and fixing any one alone achieves nothing:

1. No policies exist.
2. The application connects as a **superuser** — bypasses RLS unconditionally; `FORCE` cannot
   override it.
3. `set_tenant` has no callers, so the tenant GUC is never set.

**The dangerous asymmetry:** enabling policies without fixing the role is a *silent* no-op —
looks done, tests pass, nothing enforced. Wiring the GUC without policies returns zero rows,
which at least fails loudly.

Details in [ADR-0002](adr/0002-row-level-security.md).

## 6. `FORCE ROW LEVEL SECURITY` is not required

A claim in the first draft of ADR-0002, corrected after testing it: a `NOBYPASSRLS` role that
does not own the table is filtered by a plain `ENABLE` — verified, 1 of 2 probe rows visible,
while the owner saw both.

**Kept here because the failure mode is instructive.** Believing `FORCE` is what makes RLS work
would send someone debugging in exactly the wrong direction when a policy appears inert. The
real answer is almost always the connecting role.

## 7. The tenant GUC does not survive its transaction

Deliberate — `set_config(..., true)` is transaction-local, and that is what prevents a pooled
connection carrying one tenant's id into the next request. But two consequences are easy to miss:

- It must be set inside **every** transaction, not once per request. Code opening two sequential
  `session.begin()` blocks needs it twice.
- After the transaction ends it reads back as **`''`, not NULL**. So `nullif(…, '')` in a policy
  is not tidying — it converts a loud cast error into a silent zero-row result.

## 8. A fourth cross-tenant path, and it writes

`internal.py` states that its three endpoints are the only ones reading across tenants.
`POST /api/v1/invitations/accept` is a fourth: **public**, no principal, and it creates a user, a
membership and role grants.

**Lesson:** a comment asserting a security property is a claim someone will rely on. This one was
wrong within days of being written.

---

## 9. No service loaded its configuration file

**Impact: every setting silently fell back to its default.**

`env_file=".env"` resolves relative to the working directory. Every documented way of running a
service — the make target, the Dockerfile, the end-to-end harness — starts inside
`services/<name>`, where there is no `.env`. So nothing configured was read.

**Why nobody noticed:** the defaults happen to match the development file. Change a value in
`.env`, run the service the documented way, and the change does nothing.

**What finally surfaced it:** auth-service is the one service with a setting that has no usable
default — the signing key path. It refused to start, reporting a missing key, which read as a
missing key rather than a missing config file. The end-to-end suite was the first thing to start
it from its own directory.

**Fix:** the env file is anchored to the repository root, found by walking up for the workspace
`pyproject.toml`. Production, which supplies real environment variables, is unaffected.

## The pattern

Six of the eight are **contract failures between two components**, not logic errors inside one.
Unit tests with stubs are structurally blind to them: the stub encodes what the author *believed*
the other side did.

That is the case for the end-to-end suite in [backlog.md](backlog.md), and it is why "194 tests
pass" is a weaker statement than it looks.
