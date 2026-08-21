# Toolchain — reference set

`tooling-engineer` installs and wires these, then writes the project's real `docs/TOOLCHAIN.md`
with the chosen tool, the command, and where it runs. Every row must map to a gate row or a
hook — a tool that nothing runs is worse than no tool.

Alternatives are listed as `JS/TS | Python`. Pick one per row; do not install both.

## Contracts and types

| Enforces | Tool |
|---|---|
| API schema generated from code | `zod-to-openapi` / `tRPC` \| `drf-spectacular` / `FastAPI` |
| Typed client generated from schema | `openapi-typescript` + `openapi-fetch` |
| Runtime boundary validation | `zod` \| `pydantic` |
| Schema drift fails CI | `oasdiff` breaking-change check |
| Cross-service contracts | `Pact` \| `schemathesis` |

## Database

| Enforces | Tool |
|---|---|
| Reversible migrations | `Prisma Migrate` / `Drizzle Kit` \| `Alembic` / Django migrations |
| ERD committed and current | `SchemaSpy` / `dbdocs` / `django-extensions graph_models` |
| Query plans on new paths | `EXPLAIN (ANALYZE, BUFFERS)`, captured in the migration note |
| Slow query visibility | `pg_stat_statements`, `auto_explain` in dev |
| Constraint and RLS assertions | `pgTAP` |
| Index hygiene | `pg_stat_user_indexes` unused-index report in CI |

## Frontend

| Enforces | Tool |
|---|---|
| Component workshop, all states | `Storybook` |
| Visual regression | `Chromatic` / `Playwright` screenshot assertions |
| Accessibility automation | `axe-core` via `@storybook/addon-a11y` + `@axe-core/playwright` |
| Performance budget | `Lighthouse CI` with asserted budgets |
| Bundle inspection | `rollup-plugin-visualizer` / `size-limit` |
| Token compliance | `eslint-plugin-better-tailwindcss` / `stylelint` custom rule |

## Testing depth

| Enforces | Tool |
|---|---|
| Unit + integration | `Vitest` \| `pytest` |
| E2E | `Playwright` |
| Coverage on critical paths | native coverage with per-path thresholds |
| Tests that actually assert | `Stryker` \| `mutmut` on domain logic |
| Boundary correctness | `fast-check` \| `hypothesis` |
| API fuzzing from schema | `schemathesis` |
| Load and capacity | `k6` \| `locust` |

## Security and supply chain

| Enforces | Tool |
|---|---|
| Static analysis | `Semgrep` with stack-tuned rules |
| Secret scanning, incl. history | `gitleaks` |
| Dependency vulnerabilities | `npm audit` / `osv-scanner` \| `pip-audit` |
| License compliance | `license-checker` \| `pip-licenses` |
| Container scanning | `Trivy` |
| Automated updates | `Renovate` |

## Quality automation

| Enforces | Tool |
|---|---|
| Format + lint, one config | `Biome` / `ESLint` + `Prettier` \| `Ruff` |
| Strict types | `tsc --strict` \| `mypy --strict` |
| Architecture boundaries | `dependency-cruiser` \| `import-linter` |
| Dead code | `knip` \| `vulture` |
| Commit hygiene | `commitlint` + `husky` / `lefthook` \| `pre-commit` |

## Observability

| Enforces | Tool |
|---|---|
| Structured logs with correlation + tenant | `pino` \| `structlog` |
| Distributed tracing | `OpenTelemetry` |
| Error tracking by release | `Sentry` |
| Real health checks | readiness endpoint asserting DB, cache, queue |

## Rules

- Pin every version. Automate the upgrades.
- Each tool runs in exactly one place: pre-commit for the fast ones, CI for the rest.
- Pre-commit stays under ~10 seconds on staged files, or it gets bypassed.
- Check current documentation before configuring — these tools change often.
