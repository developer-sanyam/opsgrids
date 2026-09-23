# Testing

How tests work here, what they cover, and — more usefully — what they do not.

```bash
make check          # lint + typecheck + every Python suite; the fast backend path
make frontend-gate  # every frontend row: types, lint, boundaries, tests, builds, audit, licences
make gate           # every row CI runs, run locally — needs `make infra` and a migrated database
make gate-full      # gate + the end-to-end journey (and later: images, visual, a11y)
```

With GitHub Actions unavailable, `make gate` is the only full verification there is. Every CI
step calls one of the targets `gate` runs, so the two cannot disagree about what "green" means.
The full list of tools, what each enforces and where it runs is in
[TOOLCHAIN.md](TOOLCHAIN.md).

---

## Counts

Collected, not hand-maintained — regenerate them rather than edit them (2026-09-23):

| Suite | Tests | Command |
|---|---|---|
| Services (35, each in its own process) | 4,756 | `make test-services` |
| `libs/platform-core` | 341 | `make test-libs` |
| Repository tooling (`tests/infra`) | 37 | `make test-infra` |
| End-to-end journeys (`e2e/`) | 53 | `make e2e` |
| Frontend (14 packages, 198 files, vitest) | 2,061 | `make frontend-test` |

Python counts are `pytest --collect-only`; the frontend count is vitest's own total.

## What passing tests do not mean

Every defect in [discoveries.md](discoveries.md) passed a green suite. Most were **contract
failures between two components** — account-service served none of the internal endpoints auth
and the gateways call; the permission-version counter was never incremented so revocation did
nothing.

Unit tests with stubs are structurally blind to this class of bug, because the stub encodes what
the author *believed* the other side did. Both sides can be green and disagree. Two things now
exist for exactly that reason:

- **The end-to-end suite** (`e2e/`, `make e2e`) boots six real services, an outbox relay and a
  consumer, and drives one organization's whole journey over HTTP against real Postgres and
  RabbitMQ. It needs `make infra` and its own database (`opsgrids_wt`, see the Makefile), which
  is why it is in `gate-full` rather than `gate`.
- **Cross-service contract tests** in `services/approval/tests/` drive another service's real
  write and consume paths (see the note on `test-services` in the Makefile).

Neither covers every pair of services. A new service-to-service call needs one or the other.

## Rules that are not negotiable

### Real PostgreSQL, never SQLite
The schema depends on `citext`, partial unique indexes, JSONB, PostGIS and row-level security. A
test that passes against SQLite proves nothing about the database this ships on. `make infra`
must be up.

**Never point a test run at a database someone else is using.** The service fixtures truncate
their schema's tables. Run DB-backed suites against the local stack or an isolated database.

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
`conftest` overrides the session dependency so the HTTP client shares the test's session. Tests
must override *through* the tenant chokepoint, not around it — otherwise every test bypasses the
isolation mechanism by construction, the suite stays green, and production is unprotected.
ADR-0002 calls this out as a design constraint rather than a test cleanup task.

### A skipped test fails the build
`make hygiene` refuses `pytest.mark.skip` and a `skipif` without a reason; the shared ESLint
preset refuses `it.only`, `describe.skip`, `it.todo` and their relatives in the frontend. A
frontend package with tests runs `vitest run` without `--passWithNoTests`, so a suite that stops
being collected fails rather than passing with zero. The one package that keeps the flag,
`opsgrids-internal`, genuinely has no tests yet — that is a gap, listed below.

## What is worth testing here

Ordered by how much a failure would cost:

1. **Negative authorization.** For every endpoint: the caller who should be refused. More valuable
   than the happy path — the happy path breaks loudly and gets noticed.
2. **Cross-tenant.** Seed in org A, call with an org-B token, assert never `200`. Fifteen services
   carry a `test_cross_tenant_isolation.py`; `libs/platform-core/tests/test_tenancy.py` covers the
   transaction-local GUC itself; the end-to-end journey proves it through the whole stack.
3. **Fail-closed behaviour.** Kill Redis and assert the request is *refused*, not admitted.
   `test_the_limiter_fails_closed` exists for exactly this reason.
4. **Contract shape between services.** The fields one service reads from another by name. The
   internal-API tests assert every field `HttpDirectory` parses, so a rename breaks a test rather
   than sign-in.
5. **Idempotency.** Deliberately redeliver and assert the effect happens once.

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

- **Cross-tenant isolation is per service, not universal.** Fifteen services have the suite; the
  rest rely on the shared chokepoint and RLS. A service without one has not proven its own lists.
- **`opsgrids-internal` has no frontend tests** — the one package still on `--passWithNoTests`.
- **No load or soak testing.**
- **No mutation testing**, so the *quality* of these assertions is unmeasured.
- **No coverage thresholds.** Nothing fails when a critical path loses its tests.

## The quality gate

Every row is a `make` target, every CI step calls one, and `make gate` runs them all. A row that
has no tooling is reported **N/A with its reason**, never green.

| Row | Command |
|---|---|
| Lint and format (Python) | `make lint` — ruff, including the bandit (`S`) rules |
| Types (Python) | `make typecheck` — mypy strict over libs, every service, the tooling |
| Python tests | `make test-libs`, `make test-services`, `make test-infra` |
| No skips, debugger calls, print, bare noqa | `make hygiene` |
| Service layering, no import cycles | `make boundaries` — contracts in `.importlinter` |
| API schema and permission manifests committed and matching | `make openapi-check`; `make openapi` regenerates |
| Migration rollback | `make migration-roundtrip` — `upgrade head`, `downgrade base`, `upgrade head`, per service |
| Models match migrations | `make migration-check` — `alembic check` per service; after the roundtrip |
| Boot and health | `make smoke` — the real entrypoint under uvicorn; `/health`, then `/health/ready` |
| Shell scripts | `make shellcheck` — pinned 0.11.0, every severity |
| Dockerfiles | `make hadolint` — pinned 2.15.1, `.hadolint.yaml` |
| Dependency audit | `make audit` (pip-audit), `make frontend-audit` (pnpm audit `--prod`, expiring allow-list) |
| Licences | `make licenses`, `make frontend-licenses` — both in `tools/licenses.toml` |
| Secrets | `make secrets` — detect-secrets over every tracked file |
| Frontend types | `make frontend-typecheck` |
| Frontend lint, static accessibility, focused/skipped tests, ports | `make frontend-lint` — shared preset with jsx-a11y and the vitest rules; `tools/frontend_ports.py` |
| Frontend package boundaries, no cycles | `make frontend-boundaries` — dependency-cruiser, `frontend/.dependency-cruiser.cjs` |
| Frontend tests | `make frontend-test` |
| Production build | `make frontend-build` — every app, and the storefront's buyer bundle |
| End-to-end | `make e2e` — in `gate-full`, not `gate` |

What has no tooling yet, and why:

- **Visual regression** and **runtime accessibility** (axe over rendered states) — there is no
  component workshop to run them over. `jsx-a11y` catches what is visible in the markup; it
  cannot see contrast, focus order or live-region behaviour. `gate-full` is where they land.
- **Performance budget** — no bundle-size or field-metric budget is enforced.
- **Design-token lint** — nothing rejects a raw colour or spacing value in feature code.
- **Query plans** — nothing checks `EXPLAIN` output. It needs representative data volumes and an
  agreed baseline to compare against, and neither exists yet.
- **Container image scan** — `gate-full`'s next extension point.

Several tools carry allow-lists, and the rule for all of them is the same: allow the specific
thing with a reason, never loosen the check. Licences are allow-listed by name in
`tools/licenses.toml`; accepted npm advisories live in `frontend/audit-allowlist.toml` with an
expiry date; the dev placeholders the secret scan must ignore are patterns in the Makefile, each
with its reason, and a hard-coded test credential carries the `pragma: allowlist secret` marker
on its own line; a deliberate shellcheck, hadolint or jsx-a11y finding is disabled on its own
line with the reason beside it.
