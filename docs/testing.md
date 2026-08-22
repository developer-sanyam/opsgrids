# Testing

How tests work here, what they cover, and — more usefully — what they do not.

```bash
make test         # every service, each in its own process
make check        # lint + typecheck + test; the fast path
make gate         # check plus the rest of the quality gate; everything CI runs
```

**216 tests pass.** Read the next section before taking comfort from that.

---

## What 216 passing tests does not mean

Every defect in [discoveries.md](discoveries.md) passed a green suite. Six of the eight were
**contract failures between two components** — account-service served none of the internal
endpoints auth and the gateways call; the permission-version counter was never incremented so
revocation did nothing.

Unit tests with stubs are structurally blind to this class of bug, because the stub encodes what
the author *believed* the other side did. Both sides can be green and disagree.

**There is no test that runs two services together.** That is the single largest hole in this
suite, and it is the top item in [backlog.md](backlog.md).

## Counts

| Unit | Tests |
|---|---|
| `libs/platform-core` | 49 |
| `account-service` | 78 |
| `auth-service` | 41 |
| `app-gateway` | 31 |
| `admin-gateway` | 17 |

## Rules that are not negotiable

### Real PostgreSQL, never SQLite
The schema depends on `citext`, partial unique indexes and JSONB. A test that passes against
SQLite proves nothing about the database this ships on. `make infra` must be up.

### One service per process
Every service exposes a top-level `app` package, so only one can be importable at a time. This is
why `make test` loops over services rather than running pytest once. Running pytest from the
repository root produces import errors that look like missing dependencies and are not.

### Truncate, do not wrap in a rolled-back transaction
Fixtures truncate between tests so the code under test performs its own **real commits**. Wrapping
everything in an outer transaction that gets rolled back hides exactly the behaviour that matters
for locking, constraints and anything that reads its own writes.

This caught a real defect: the failed-sign-in counter was being staged in the caller's
transaction, so the rollback that accompanied a rejected sign-in discarded it and **account
lockout never fired**. A rolled-back-transaction fixture would have hidden that permanently.

### Never override the thing you are testing
`conftest` overrides the session dependency so the HTTP client shares the test's session. Once the
tenant chokepoint lands, tests must override *through* it, not around it — otherwise every test
bypasses the isolation mechanism by construction, the suite stays green, and production is
unprotected. This is called out in ADR-0002 as a design constraint rather than a test cleanup
task.

## What is worth testing here

Ordered by how much a failure would cost:

1. **Negative authorization.** For every endpoint: the caller who should be refused. More valuable
   than the happy path — the happy path breaks loudly and gets noticed.
2. **Cross-tenant.** Seed in org A, call with an org-B token, assert never `200`.
3. **Fail-closed behaviour.** Kill Redis and assert the request is *refused*, not admitted.
   `test_the_limiter_fails_closed` exists for exactly this reason.
4. **Contract shape between services.** The fields one service reads from another by name. The
   internal-API tests assert every field `HttpDirectory` parses, so a rename breaks a test rather
   than sign-in.
5. **Idempotency**, once consumers exist: deliberately redeliver and assert the effect happens
   once.

## Test-writing conventions

**Name the behaviour, not the function.**
`test_a_non_staff_caller_gets_404_not_403` says what is guaranteed and why the reader should
care. `test_staff_check` does not.

**Put the reasoning in the docstring.** Several tests here explain the attack they prevent — the
404-instead-of-403 test explains that a `403` would confirm the route exists to whoever is
probing. That reasoning is why the test may not be "simplified" later.

**Assert the negative too.** `assert not upstream.calls` next to a status assertion is what
distinguishes "refused" from "refused after forwarding it anyway".

## Gaps

- **No end-to-end suite** — the top gap.
- **No cross-tenant isolation suite** — the acceptance gate for tenancy.
- **No frontend tests at all** — no product UI exists to test.
- **No load or soak testing.**
- **No mutation testing**, so the *quality* of these assertions is unmeasured.
- **7 of the 18 quality-gate rows have no tooling.** They must be reported **N/A with a
  reason**, never green. The rest are `make` targets, each a named CI step, and `make gate`
  runs them all.

## The quality gate

What has tooling, and what it is:

| Row | Check | Command |
|---|---|---|
| 5 | Migration rollback | `make migration-roundtrip` — `upgrade head`, `downgrade base`, `upgrade head`, per service |
| 7 | Boot and health | `make smoke` — the real entrypoint under uvicorn; `/health`, then `/health/ready` |
| 10 | No secrets, skips or debug in the diff | `make secrets` and `make hygiene` |
| 11 | API schema committed and matching | `make openapi-check`; `make openapi` regenerates `libs/contracts/openapi/` |
| 16 | SAST, secret scan, dependency audit, licence check | `make lint` (ruff bandit rules), `make secrets`, `make audit`, `make licenses` |
| 17 | Dependency boundaries, no import cycles | `make boundaries` — the contracts are in `.importlinter` |

What does not, and why:

- **Rows 6, 8, 12, 13, 14, 18** — production build, end-to-end, visual regression,
  accessibility, performance budget, design-token lint. There is no product UI and no E2E
  harness to run any of them against; they arrive with the first one.
- **Row 15** — query plans. Nothing checks `EXPLAIN` output. It needs representative data
  volumes and an agreed baseline to compare against, and neither exists yet.

Two of the tools carry allow-lists, and the rule for both is the same: allow the specific
thing with a reason, never loosen the check. Licences are allow-listed by name in
`tools/licenses.toml`; the dev placeholders the secret scan must ignore are patterns in the
Makefile, each with its reason, and a hard-coded test credential carries the
`pragma: allowlist secret` marker on its own line.
