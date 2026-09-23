# Toolchain

Every tool that turns one of this project's rules into a command that exits non-zero: what it
enforces, how to run it, where it runs, and why it is worth its install time.

**Where it runs.** `gate` = `make gate` (needs `make infra` and a migrated database for its last
rows); `gate-full` = `make gate-full`; CI = the named job in `.github/workflows/ci.yml`. Every CI
check is a make target that `gate` also runs — GitHub Actions is currently unavailable, so the
local gate is the verification, and CI is the same targets on someone else's machine. There are
no pre-commit hooks and no scheduled runs yet (see "Not yet installed").

**Versions.** Python tools are pinned by `uv.lock`, npm tools by `frontend/pnpm-lock.yaml` (new
tools at exact versions in `package.json`). shellcheck and hadolint are pinned in the Makefile
and run from their pinned images whenever the local binary is a different version.

## Backend

| Tool | Enforces | Command | Where | Why it earns its place |
|---|---|---|---|---|
| ruff | Lint + format, bandit (`S`) security rules, no print/breakpoint/bare noqa | `make lint`, `make hygiene` | gate, CI static | One tool for formatter, linter and the SAST rules this stack needs. |
| mypy (strict, pydantic plugin) | Types in libs, every service, the tooling | `make typecheck` | gate, CI static | The service contracts are Pydantic; a type error is a contract error. |
| import-linter | Service layering (`api → services → selectors → models`), no cycles | `make boundaries` | gate, CI static | The service-anatomy rules in CLAUDE.md, mechanically. |
| tools/hygiene.py | No `pytest.mark.skip`, no `skipif` without a reason | `make hygiene` | gate, CI static | The one hygiene rule ruff has no rule for. |
| pytest | libs, services, repository tooling | `make test-libs`, `make test-services`, `make test-infra` | gate, CI tests / static | Against real Postgres, never SQLite (see testing.md). |
| tools/export_openapi.py, export_permissions.py | Committed OpenAPI + permission manifests match the code | `make openapi-check` | gate, CI static | The schema is generated from the implementation, so it cannot drift silently. |
| alembic | Migrations reverse cleanly; models match migrations | `make migration-roundtrip`, `make migration-check` | gate, CI migrations | A downgrade nobody ran is a rollback nobody has. |
| tools/smoke.py | The real entrypoint boots and `/health/ready` answers | `make smoke` | gate, CI tests (shard 1) | The only check that runs uvicorn, the lifespan and the pool for real. |
| tools/shard-verify.sh | CI's shards partition the service list exactly | `make shard-verify SHARDS=3` | gate, CI static | A miscount silently drops a service's tests. |
| e2e harness (pytest) | One organization's journey across six real services | `make e2e` | gate-full | The only test of the contracts *between* services end to end. |

## Frontend

| Tool | Enforces | Command | Where | Why it earns its place |
|---|---|---|---|---|
| TypeScript (`tsc --noEmit`, strict) | Types in every app and package | `make frontend-typecheck` | gate, CI frontend | `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes`, as frontend/CLAUDE.md requires. |
| ESLint, one shared preset (`frontend/opsgrids-config/eslint.preset.js`) | typescript-eslint, react-hooks, react-refresh; `--max-warnings=0` | `make frontend-lint` | gate, CI frontend | ADR-0001's "one eslint config" — a rule added once holds in fourteen packages. |
| eslint-plugin-jsx-a11y (recommended) | Labels tied to controls, no div-as-button, valid ARIA | `make frontend-lint` | gate, CI frontend | frontend/CLAUDE.md's semantic-HTML rules, checked in the markup. |
| @vitest/eslint-plugin | No `.only`, `.skip`, `.todo` (`no-focused-tests`, `no-disabled-tests`, `warn-todo`) | `make frontend-lint` | gate, CI frontend | The frontend half of tools/hygiene.py; vitest passes a focused suite. |
| tools/frontend_ports.py | Every app has its own dev and preview port, agreeing with vite.config | `make frontend-lint` | gate, CI frontend | Seven apps once shared a preview port; stdlib, no install. |
| dependency-cruiser | Apps never import each other; `@opsgrids/ui` / `@opsgrids/client` only via exports; ui ⟂ client; no cycles | `make frontend-boundaries` | gate, CI frontend | The frontend's `.importlinter`; found a real cycle in the storefront on day one. |
| vitest | Frontend tests; `--passWithNoTests` only where a package has none | `make frontend-test` | gate, CI frontend | Already the test runner; the change is that an empty suite fails. |
| Vite | Production builds, including the storefront's buyer bundle | `make frontend-build` | gate, CI frontend | The bundle buyers load was never built by any check before. |

## Supply chain

| Tool | Enforces | Command | Where | Why it earns its place |
|---|---|---|---|---|
| pip-audit | No known vulnerability in what `uv.lock` resolves to | `make audit` | gate, CI supply-chain | Audits the lock, not the laptop. |
| pnpm audit + tools/npm_audit.py | No unaccepted high/critical advisory in the production npm tree; accepted ones expire | `make frontend-audit` | gate, CI supply-chain | pnpm's own ignore list has no reason and no expiry; `frontend/audit-allowlist.toml` has both. |
| pip-licenses + tools/licenses.py | Every Python dependency's licence is allow-listed | `make licenses` | gate, CI supply-chain | Copyleft travels with every copy of a product that is sold on. |
| pnpm licenses + tools/licenses.py `--npm` | Every production npm package's licence is on the `[npm]` allow-list | `make frontend-licenses` | gate, CI supply-chain | Same rule and file as the Python side; one parser for both. |
| detect-secrets | No credential in any tracked file, against `.secrets.baseline` | `make secrets` | gate, CI supply-chain | Placeholders excluded by pattern with reasons, never by detector. |
| shellcheck 0.11.0 | Every shell script and service entrypoint, every severity | `make shellcheck` | gate, CI static | The deploy path runs as root on the production box. |
| hadolint 2.15.1 | Every Dockerfile, threshold `style` (`.hadolint.yaml`) | `make hadolint` | gate, CI static | 35 Dockerfiles from one template: fix once, or ignore once with a reason. |

## Installing

```bash
make install                 # Python toolchain (uv sync)
# Node 22 and pnpm 11 on PATH, then:
make frontend-install        # pnpm install --frozen-lockfile
brew install shellcheck hadolint   # optional — without them the pinned Docker images are used
```

## Not yet installed

Named so their absence is a known gap rather than an assumption: pre-commit hooks on staged
files; automated dependency-update pull requests; coverage thresholds on critical paths; mutation
testing; property-based and schema-driven API fuzzing; load testing; container image scanning,
visual regression and runtime (axe) accessibility — the last three are `gate-full`'s extension
points; a performance budget; a design-token lint rule; query-plan capture; ERD generation;
dead-code detection; commit-message linting.
