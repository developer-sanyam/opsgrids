# Testing

How tests work here, what they cover, and — more usefully — what they do not.

```bash
make check          # lint + typecheck + every Python suite; the fast backend path
make frontend-gate  # every frontend row: types, lint, boundaries, tests, builds, audit, licences
make gate           # every row CI runs, run locally — needs `make infra` and a migrated database
make gate-full      # gate + the end-to-end journey + screenshots and axe (images later)
```

With GitHub Actions unavailable, `make gate` is the only full verification there is. Every CI
step calls one of the targets `gate` runs, so the two cannot disagree about what "green" means.
The full list of tools, what each enforces and where it runs is in
[TOOLCHAIN.md](TOOLCHAIN.md).

---

## Counts

Collected, not hand-maintained — regenerate them rather than edit them (2026-10-15, `main` @ `1dbeebd4`):

| Suite | Tests | Command |
|---|---|---|
| Services (34, each in its own process) | 17,734 collected | `make test-services` |
| `libs` (`platform-core` 928, plus `contracts`) | 954 collected | `make test-libs` |
| Repository tooling (`tests/infra`) | 1,242 collected (50 of them need a database, below) | `make test-infra`, `make test-infra-db` |
| End-to-end journeys (`e2e/`) | 76 collected | `make e2e` |
| Frontend (vitest) | 9,059 (18 packages; on Node 22.14, two timing-sensitive tests failed in the full parallel run and passed alone) | `make frontend-test` |

Python counts: `uv run pytest --collect-only -q`, run inside each `services/<name>` for the services
(the service total is the sum over all 34). The frontend count is vitest's own total.

Some `libs/platform-core` integration tests need RabbitMQ or MailHog and fail without `make infra`.
Run the frontend suites on Node 22 (`engines` is `>=22`). On Node 24.19, 13 tests in `opsgrids-ui` and
`opsgrids-client` (router leave-guards and the SSO loop) failed on 2026-10-15; they pass on 22.14.

## How CI runs this, and how long it takes

Decisions and the reasoning behind them: [ADR-0040](adr/0040-faster-ci.md). Every row is still a `make`
target that `make gate` runs; what changed is how the rows are spread over runners.

| Job | Runs |
|---|---|
| `Changed areas` | Which half of the repository a pull request touches, which services, which of the test shards have anything affected |
| `Backend — lint, boundaries, schemas` | `lint`, `hygiene`, `boundaries`, both shard-coverage checks, `openapi-check`, `shellcheck`, `hadolint` |
| `Backend — types` | `mypy` over libs, every service, the tooling (the mypy cache is saved even when the job is red) |
| `Backend — tooling tests` | `make test-infra` |
| `Backend — tests 1/6` also | `make test-infra-db`: the 50 database-backed `tests/infra` tests, in their own `opsgrids_tooling` database (below) |
| `Backend — migrations` | Round-trip and drift check for every service, two at a time |
| `Backend — tests N/6` | Its share of the service suites (below); shard 1 also runs the library tests and the boot smoke |
| `Contracts`, `Supply chain` | As before; supply-chain rows now each report even if an earlier one is red |
| `Frontend — types, lint, boundaries`, `Frontend — tests and build N/4` | The workspace, four slices at once |

### The database-backed `tests/infra` tests

Four modules in `tests/infra` run the destructive tools and the put-away runbook queries against a real, migrated
Postgres: `test_purge_test_orgs_db.py` (47 tests; `tools/purge_test_orgs.py`), `test_purge_putaway_captures.py` (1),
`test_putaway_docs.py` (1) and `test_runbook_putaway_coordinate_check.py` (1). Each skips unless its variable is set
(`OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL`, `OPSGRIDS_PURGE_TEST_DATABASE_URL`, `OPSGRIDS_RUNBOOK_TEST_DATABASE_URL`), so
plain `make test-infra` (no database) skips them and says so. Until 2026-10-15 nothing set the variables, so they never
ran anywhere.

```bash
make infra                # the compose Postgres
make test-infra-db        # create opsgrids_tooling, migrate every service into it, run the four modules
make tooling-db           # just the idempotent database + grants (tools/dev-bootstrap/tooling-db.sql)
```

Any other local cluster: `make test-infra-db TOOLING_DB_HOST=127.0.0.1 TOOLING_DB_PORT=5446
TOOLING_DB_PSQL="psql -h 127.0.0.1 -p 5446 -U opsgrids -d postgres" TOOLING_DB_INIT=tools/dev-bootstrap/init-db.sql`
(the superuser must be able to create databases; a host-installed PostGIS is fine). CI runs the same target on shard 1
of the backend tests job, against the job's Postgres service.

The purge-orgs tests connect as the owner role `opsgrids_admin`; the other two seed rows with
`SET session_replication_role`, which needs the superuser, so they get the superuser DSN. The database is a throwaway
by construction: `tests/infra/_helpers.py::real_database_dsn` fails collection if a DSN names the shared `opsgrids`
database or any host that is not loopback (`test_real_database_guard.py` pins that). Each test builds and removes its own
random organisations, so a second run in the same database is safe; run one session per database at a time. When a
migration adds a CHECK constraint a generic seeder cannot satisfy, `test_the_cast_has_a_row_in_nearly_every_table_the_tool_reaches`
fails naming the tables; add a `FIXUPS` entry in `tests/infra/_purge_orgs_world.py`.

### The service tests are sharded by item

An *item* is a whole service or one part of one. `tools/ci_shards.py` cuts any service that outweighs half a shard's
share into parts by recorded per-file duration ([`tools/test-durations.json`](../tools/test-durations.json)), and deals
the items out longest-first. Parts of one service never share a shard, so never a database: each shard is a runner
with its own Postgres, and every service fixture `TRUNCATE`s its schema before each test.

```bash
make test-shard-plan                 # the split, with estimated seconds per shard
make test-shard-verify               # every test file is in exactly one item (a CI static row)
make test-shard SHARD=3 SHARDS=6     # run one shard's items, as CI does; AFFECTED_SERVICES narrows it
```

**Re-balancing.** The durations are a measurement and go stale as suites grow; a stale file never drops a test (every
file still runs exactly once), it only skews the balance — and an entry for a file that was renamed or deleted is a
`note:` from `verify`, not a failure; `refresh` prunes it. Each shard uploads its junit timings as `junit-shard-N`
(kept 14 days):

```bash
gh run download <run-id> -p 'junit-shard-*' -D /tmp/junit
python3 tools/ci_shards.py refresh --run https://github.com/developer-sanyam/opsgrids/actions/runs/<run-id> /tmp/junit
make test-shard-plan                               # check the estimate, then commit
```

`--run` is what lands in `source` at the top of `tools/test-durations.json`, with the date: the artifacts expire and a
`/tmp` path means nothing to the next reader, so the run's URL is the thing to record.

Changing `TEST_SHARDS` (top of `ci.yml`, and the Makefile default — a test keeps them equal) changes the number of
runners. Look at `make test-shard-plan TEST_SHARDS=<n>` first; below the point where one item is a whole shard there is
nothing left to gain.

### Why the CI database runs without fsync

The fixtures truncate about fifty tables before every test, and a service suite is mostly those commits.
`tools/ci-postgres-tune.sh` turns `fsync`, `synchronous_commit` and `full_page_writes` off on the throwaway CI
database (and refuses to run unless `CI=true`). It is 25–40% faster on a GitHub runner (`operations` 1,138 s → 838 s,
`finance` 125 → 84 s); on a laptop's Docker VM the same change is 2.4×, which is a reason to measure on the runner.

### Measured timings

Seven recent runs before the change, wall clock: 22.9, 37.1, 30.0, 28.6, 36.1, 33.3, **32.5** minutes. None was green.
The table compares the latest of them (a `main` push) with the first run of the sharded pipeline (a push to the branch through a temporary trigger, since removed,
whole pipeline, same trigger shape as `main`). Rows marked *stopped* ended at their first red step, so their
later rows did not run — the "after" column shows what those rows cost once they were allowed to report.

| Job | Before — [run 37452127273](https://github.com/developer-sanyam/opsgrids/actions/runs/37452127273) | After, round 1 — [run 37466031956](https://github.com/developer-sanyam/opsgrids/actions/runs/37466031956) |
|---|---|---|
| Wall clock | **32.5 min** | **13.0 min** |
| Changed areas | 5 s | 8 s |
| Lint, types, contracts | 13 s (*stopped* at lint) | 716 s, every row ran: types 415 s (cold cache), tooling tests 221 s, OpenAPI 44 s |
| Migrations | 85 / 99 / 121 s (3 jobs) | 73 / 88 / 103 s (3 jobs) |
| Service tests | 448 / 750 / **1,920** s (3 jobs) | 462 / 352 / 614 / 400 / **764** s (5 jobs) |
| Contracts | 20 s | 16 s |
| Supply chain | 38 s (*stopped* at the advisory scan) | 244 s, every row ran: secret scan 209 s |
| Frontend | 1,151 s (*stopped*: tests red, build skipped) | static 341 s; four legs 326 / 201 / 197 / 299 s |
| Job time summed | 77.5 min | 86.7 min |

Reading it: the critical path went from the 32-minute test shard to the 12-minute static job (cold mypy plus the
tooling tests, in series) and the 12.7-minute test shard that the seeded durations left heavier than the rest. The
summed job time is higher only because rows that used to be skipped (the secret scan, the build, mypy, the tooling
tests) now run; the same rows, counted in the "before" column, would put it near 95 minutes.

**Round 2** (pushed, but GitHub refused to start the jobs: *"recent account payments have failed or your spending
limit needs to be increased"*) re-deals the shards from the durations measured in round 1, splits the static job into
three, and merges the three migrations jobs into one. It is a prediction, not a measurement, until a run proves it:

| | Predicted |
|---|---|
| Six test shards, estimated test time | 363 – 367 s each (every shard within 4 s), plus ~95 s setup: ~7.7 min |
| Static, types (warm cache), tooling tests | 1.5 – 4 min each, in parallel |
| Frontend | ~5 min |
| Wall clock | **~8 min** |

Per-shard overhead measured in round 1 (what the six shards pay and the old three did not): containers 27–40 s, migrate
25–42 s, checkout and `uv` ~10 s, artifact upload ~4 s.

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
2. **Cross-tenant.** Seed in org A, call with an org-B token, assert never `200`. Seventeen services
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

- **Cross-tenant isolation is per service, not universal.** Seventeen of 34 services have the suite; the
  rest rely on the shared chokepoint and RLS. A service without one has not proven its own lists.
- **`opsgrids-internal` has no frontend tests** — the one package still on `--passWithNoTests`.
- **No load or soak testing.**
- **No mutation testing**, so the *quality* of these assertions is unmeasured.
- **No coverage thresholds.** Nothing fails when a critical path loses its tests.

## Frontend visual, accessibility and layout checks

Details and every variable: [`frontend/VISUAL-TESTING.md`](../frontend/VISUAL-TESTING.md). Run from
`frontend/` with Node 22 or newer and pnpm 11.

```bash
pnpm visual:check     # screenshots + axe against opsgrids-visual/baselines/darwin (2,454 PNGs)
pnpm visual:update    # accept a reviewed change: rewrites baselines and a11y-baseline.json
pnpm --filter @opsgrids/visual run sweep   # the layout sweep: all 14 apps at 320/360/390/414 px
SWEEP_APPS=product-finance SWEEP_ROUTE=/invoices SWEEP_WIDTHS=390 pnpm --filter @opsgrids/visual run sweep
```

The sweep's mobile categories apply below 768 px: `g` touch target under 44x44, `h` text field under
16px (iOS zooms), `j` dialog, drawer or menu larger than the viewport or with its primary action
off-screen, `k` table wider than the viewport with no scroll container, `l` fixed bar without
safe-area insets or covering content, `m` text under 12px. Categories `a` to `f` (page overflow,
escaped boxes, misaligned rows, clipped text, no bottom space) run at every width. The sweep writes
`report.json` and screenshots under `opsgrids-visual/test-results/sweep`. Fix a hit in `opsgrids-ui`
first and in the app second.

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

- **Visual regression and runtime accessibility** exist, but only locally and only on macOS: `make
  ui-visual` (part of `gate-full`) takes screenshots and runs axe over 402 workshop stories and
  7 app pages, at 375, 768 and 1280 px, in both themes. There are no Linux baselines, so it does
  not run in CI. See [`frontend/VISUAL-TESTING.md`](../frontend/VISUAL-TESTING.md).
- **The layout sweep** is a developer tool, not a gate row. It walks every route of 14 apps in sample
  mode and flags layout defects (see the section above the gate table).
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
