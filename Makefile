# OpsGrids Platform — developer entry points.
.DEFAULT_GOAL := help
SHELL := /bin/bash

# Services are discovered, not listed: a new service directory is picked up with no edit here.
SERVICES := $(notdir $(wildcard services/*))
COMPOSE  := docker compose -f tools/dev-bootstrap/compose.yml --env-file .env

# How many services to process at once in the per-service fan-outs below. Thirty-odd
# invocations of mypy, pytest or alembic run one at a time is most of what CI spends its
# time on, and each invocation is an independent process against its own directory and its
# own database schema — so they are parallelisable, not merely parallel-looking.
#
# `JOBS=1` restores the previous strictly-serial behaviour exactly, which is the escape
# hatch if a parallel run is ever suspected of hiding something.
JOBS ?= $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# Run one command per service, `JOBS` at a time. See tools/fan-out.sh for why this is a
# script rather than an inline xargs pipeline, and why it is safe to parallelise at all.
#
#   $(1) a path that must exist inside services/<name>, or the service is skipped
#   $(2) `ok-empty` to accept pytest's "no tests collected", otherwise `strict`
#   $(3) the command, run with services/<name> as the working directory
define fan_out
	@tools/fan-out.sh $(JOBS) $(1) $(2) $(3)
endef

# Every per-service target runs from services/<name>, and a service's settings read `.env`
# relative to the working directory — so without this, `make migrate` and `make test` never
# see the root .env and silently fall back to the built-in defaults, which point at the
# shared dev database whatever the developer configured. Export exactly the names the file
# defines, so make's own variables stay out of the environment. CI has no .env and sets its
# variables in the environment, so its absence is fine.
ifneq (,$(wildcard .env))
include .env
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env)
endif

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup
.PHONY: install
install: ## Install Python dependencies
	uv sync

.PHONY: devkeys
devkeys: ## Generate a dev Ed25519 signing keypair for auth-service
	@mkdir -p tools/dev-bootstrap/keys
	@test -f tools/dev-bootstrap/keys/dev-ed25519.pem || \
	  openssl genpkey -algorithm ed25519 -out tools/dev-bootstrap/keys/dev-ed25519.pem
	@echo "key at tools/dev-bootstrap/keys/dev-ed25519.pem (gitignored)"

.PHONY: infra
infra: ## Start postgres+postgis, redis, rabbitmq, minio, mailhog
	$(COMPOSE) up -d

.PHONY: infra-down
infra-down: ## Stop the infrastructure containers
	$(COMPOSE) stop

.PHONY: infra-reset
infra-reset: ## Destroy and recreate the data volumes (destructive)
	$(COMPOSE) down -v
	$(MAKE) infra

# ---------------------------------------------------------------- database
.PHONY: migrate
migrate: ## Apply migrations for every service
	# Safe to run concurrently because of ADR-0000 §10: no foreign key crosses a schema
	# boundary, so each service's DDL touches only its own schema and there is no ordering
	# between them. The shared objects — extensions, roles — are created once by
	# `tools/dev-bootstrap/init-db.sql` before any of this runs.
	$(call fan_out,alembic.ini,strict,uv run alembic upgrade head)

.PHONY: reconcile-roles
reconcile-roles: ## Create any opsgrids_<service> role missing on this dev database (idempotent)
	tools/reconcile-roles.sh

.PHONY: makemigration
makemigration: ## Autogenerate: make makemigration SERVICE=account M="add memberships"
	@test -n "$(SERVICE)" || (echo "SERVICE= is required" && exit 1)
	@test -n "$(M)" || (echo 'M="message" is required' && exit 1)
	cd services/$(SERVICE) && uv run alembic revision --autogenerate -m "$(M)"

.PHONY: migration-check
migration-check: ## Fail if a model changed without a migration
	# Read-only: `alembic check` autogenerates in memory and compares, emitting no DDL.
	$(call fan_out,alembic.ini,strict,uv run alembic check)

# A downgrade() nobody has run is a rollback nobody has. Going to base and back proves each
# one actually reverts what its upgrade() did — against the configured database, so run it
# where you would run `make migrate`. env.py re-applies the schema grants after every step.
.PHONY: migration-roundtrip
migration-roundtrip: ## upgrade head → downgrade base → upgrade head, every service
	# The same per-schema independence as `migrate`, so the fan-out applies here too. This
	# is the strictest thing in the suite — a downgrade that does not revert is found here
	# rather than during a rollback — and the assertions are unchanged: every service still
	# goes all the way up, all the way down and all the way up again. Only the scheduling
	# differs, and `JOBS=1` puts it back.
	$(call fan_out,alembic.ini,strict,$(CURDIR)/tools/roundtrip.sh)

# ---------------------------------------------------------------- run
.PHONY: run
run: ## Run one service: make run SERVICE=account PORT=8111
	@test -n "$(SERVICE)" || (echo "SERVICE= is required" && exit 1)
	cd services/$(SERVICE) && uv run uvicorn app.main:app --reload --port $${PORT:-8110}

.PHONY: worker
worker: ## Run one service's event consumer: make worker SERVICE=account
	@test -n "$(SERVICE)" || (echo "SERVICE= is required" && exit 1)
	cd services/$(SERVICE) && uv run python -m app.consumers

.PHONY: relay
relay: ## Run one service's outbox relay: make relay SERVICE=account
	@test -n "$(SERVICE)" || (echo "SERVICE= is required" && exit 1)
	cd services/$(SERVICE) && uv run python -m app.relay

# The third entrypoint. Only services with scheduled work have an `app/cron.py` —
# subscription's dunning sweep (ADR-0005) is the first.
.PHONY: cron
cron: ## Run one service's scheduled sweep: make cron SERVICE=subscription
	@test -n "$(SERVICE)" || (echo "SERVICE= is required" && exit 1)
	cd services/$(SERVICE) && uv run python -m app.cron

# Tests build the app in-process with its dependencies overridden. This is the one check
# that runs the real entrypoint — uvicorn, the lifespan, the pool, Redis — and asks it
# /health and /health/ready. Needs the infrastructure up, like the tests do.
.PHONY: smoke
smoke: ## Boot a service for real and poll its health: make smoke [SERVICE=account]
	uv run python tools/smoke.py --service $(or $(SERVICE),account)

# ---------------------------------------------------------------- scaffolding
.PHONY: new-service
new-service: ## Scaffold a service: make new-service NAME=billing SCHEMA=billing
	@test -n "$(NAME)" || (echo "NAME= is required" && exit 1)
	uv run python tools/new_service.py --name $(NAME) --schema $${SCHEMA:-$(NAME)}

# ---------------------------------------------------------------- quality gates
.PHONY: lint
lint: ## Ruff
	uv run ruff check .
	uv run ruff format --check .

.PHONY: format
format: ## Auto-fix formatting
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: typecheck
typecheck: typecheck-libs typecheck-services typecheck-tools ## mypy over libs, services, tools

.PHONY: typecheck-libs
typecheck-libs: ## mypy over the shared libraries
	uv run mypy libs

.PHONY: typecheck-tools
typecheck-tools: ## mypy over the repository tooling
	uv run mypy tools

.PHONY: typecheck-services
typecheck-services: ## mypy per service — they cannot share a process
	# `import app` is ambiguous across services, so mypy genuinely cannot check them in one
	# process. It can check them in several at once: each runs in its own directory against
	# its own `.mypy_cache`, so there is nothing to share and nothing to race.
	$(call fan_out,app,strict,uv run mypy app)

.PHONY: test
test: test-libs test-infra test-services ## Every Python suite: libs, repository tooling, services

.PHONY: test-libs
test-libs: ## Shared library tests
	uv run pytest libs

.PHONY: test-infra
test-infra: ## Repository-tooling tests: deploy scripts' path logic, the gate's own checkers
	# No database, no broker, no `make infra` — each test builds its own throwaway git repo
	# or temp dir and drives the real scripts and functions against it. Safe and fast enough
	# to run in the static job alongside lint and typecheck.
	@if [ -z "$$OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL" ]; then \
	  echo "note: the real-database tests in tests/infra (purge tools, put-away runbook) are skipped here;"; \
	  echo "      run them with 'make test-infra-db' (needs make infra). CI runs them: see docs/testing.md."; fi
	uv run pytest tests/infra -q

.PHONY: test-billing-properties
test-billing-properties: ## The billing run's randomised money invariants at 500 seeds (nightly/manual)
	# The ordinary suite runs 25 seeds so CI stays fast; this is the deep run (ADR-0005 addendum,
	# final review). Needs the database like any service test; a failure names its seed.
	cd services/subscription && BILLING_PROPERTY_SEEDS=500 uv run pytest -q -p no:cacheprovider \
	  tests/test_billing_run_properties.py

.PHONY: shard-verify
shard-verify: ## Assert SHARDS=3 partitions the service list exactly (no service dropped)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	tools/shard-verify.sh $(SHARDS)

# How many runners the service tests are spread across in CI (`tests` job matrix in
# .github/workflows/ci.yml; tests/infra/test_ci_shards.py keeps the two in step). The
# migrations jobs shard by service and keep SHARDS=3; the tests shard by *item* — a service,
# or part of one — so they have their own count and their own verifier.
TEST_SHARDS ?= 6

.PHONY: test-shard-verify
test-shard-verify: ## Assert TEST_SHARDS=6 covers every service test file exactly once
	python3 tools/ci_shards.py verify $(TEST_SHARDS)

.PHONY: test-shard-plan
test-shard-plan: ## Show the test shards and their estimated seconds: make test-shard-plan [TEST_SHARDS=6]
	@python3 tools/ci_shards.py summary $(TEST_SHARDS)

.PHONY: test-shard
test-shard: ## Tests for one shard of the service suites: make test-shard SHARD=1 SHARDS=6
	# CI splits the service tests across jobs rather than across processes, because on a
	# 2-vCPU runner a second process makes both slower (see .github/workflows/ci.yml). Each
	# shard gets its own runner and its own database, so the parallelism is real capacity.
	#
	# The unit of work is an *item* — a whole service, or one part of a service too heavy for a
	# single shard's share (`operations:2/5`) — assigned by tools/ci_shards.py from the recorded
	# durations in tools/test-durations.json. Parts of one service never share a shard, hence
	# never a database, so they cannot truncate each other's tables. `verify` (run first, and in
	# CI's static job) proves every test file is in exactly one item. Unlike the migrations, which
	# still partition by service (tools/shard.sh), this is not a partition of the service list.
	#
	# AFFECTED_SERVICES narrows what runs, exactly as before (tools/filter-affected.sh).
	@test -n "$(SHARD)" || (echo "SHARD= is required" && exit 1)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	@tools/test-shard.sh $(SHARD) $(SHARDS) $(JOBS)

.PHONY: migration-check-shard
migration-check-shard: ## Model/migration drift for one shard: SHARD=1 SHARDS=3
	@test -n "$(SHARD)" || (echo "SHARD= is required" && exit 1)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	@tools/shard-filtered.sh $(SHARD) $(SHARDS) $(JOBS) alembic.ini strict uv run alembic check

.PHONY: migration-shard
migration-shard: ## Migration round-trip for one shard: make migration-shard SHARD=1 SHARDS=3
	@test -n "$(SHARD)" || (echo "SHARD= is required" && exit 1)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	@tools/shard-verify.sh $(SHARDS)
	@tools/shard-filtered.sh $(SHARD) $(SHARDS) $(JOBS) alembic.ini strict \
	  $(CURDIR)/tools/roundtrip.sh

.PHONY: test-services
test-services: ## Each service in its own process
	# Parallel across services, never within one. Inside a service the fixtures share a
	# schema, which is why the fan-out is per service and pytest itself stays single-process.
	#
	# Across services it is parallel because each one owns a Postgres schema — *except where a
	# test deliberately crosses a service boundary*, which two do. They are not an accident to
	# be removed: they are the only tests that prove the two halves of a slice actually fit.
	#
	#   services/approval/tests/test_attendance_round_trip.py    -> the `attendance` schema
	#   services/approval/tests/test_cross_service_contract.py   -> the `employee` schema
	#
	# Each drives the other service's real write and consume paths through a subprocess helper
	# in that service's own `tests/`, so it holds live rows in a schema it does not own — while
	# that service's own `session` fixture opens every one of its tests by truncating exactly
	# those tables. Scheduled at the same time, each deletes rows the other is mid-way through
	# using, and the failures read like broken logic rather than like a scheduling bug.
	#
	# So each of those two modules, and the `session` fixture of the service it reaches into,
	# take a Postgres advisory lock named for the schema they contend for
	# (`platform_core.testing.cross_service_schema_lock`). Each pair serialises against itself
	# and nothing else; the other ~32 services stay fully parallel.
	#
	# The rule for a new service: fan-out safety comes from a suite touching only its own
	# schema. If a test reaches into a sibling's schema — directly, or through that sibling's
	# test helper — it must take the same lock on that sibling's schema, and the sibling's
	# truncating fixture must take it too. Nothing detects this for you; it shows up as a
	# flake.
	#
	# pytest's exit code 5 means "no tests collected", which is not a failure for a
	# service that has none yet — hence `ok-empty`.
	$(call fan_out,tests,ok-empty,uv run pytest tests -q -p no:cacheprovider)

# e2e/conftest.py pins DATABASE_URL to `opsgrids_wt`, a second database in the same cluster
# that nothing else creates. This does, by hand, what the postgis image's own init script did
# for `opsgrids` at initdb time — plus the database-level grants init-db.sql only ever names
# `opsgrids` for — so a service's migrations and its role can actually reach opsgrids_wt.
# Idempotent: every statement is guarded, safe to run any number of times, never drops or
# truncates anything. Needs `make infra` already up.
.PHONY: e2e-db
e2e-db: ## Create/grant the opsgrids_wt database the e2e suite targets (idempotent, needs make infra)
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U opsgrids -d opsgrids -f - \
	  < tools/dev-bootstrap/e2e-db.sql

# The same shape as e2e-db, for ops-monitor's own integration suite
# (libs/platform-core/tests/test_ops_monitor_integration.py) — never the shared `opsgrids`
# database. See tools/dev-bootstrap/ops-monitor-db.sql for why a second database.
.PHONY: ops-monitor-db
ops-monitor-db: ## Create/grant the opsgrids_mon database ops-monitor's integration tests target (idempotent, needs make infra)
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U opsgrids -d opsgrids -f - \
	  < tools/dev-bootstrap/ops-monitor-db.sql

# The same shape again, for trying the console backfill (app/backfill.py) by hand against real
# seeded data — never the shared `opsgrids` database. Migrate into it the same way `e2e`'s own
# comment describes: `make migrate DATABASE_URL=postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids_backfill`.
.PHONY: backfill-db
backfill-db: ## Create/grant the opsgrids_backfill database (idempotent, needs make infra)
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U opsgrids -d opsgrids -f - \
	  < tools/dev-bootstrap/backfill-db.sql

# The four tests/infra modules that need a real, migrated Postgres: they run the purge tools
# (tools/purge_test_orgs.py, tools/purge_putaway_captures.py, which DELETE) and the put-away
# runbook queries against it. The purge-orgs tests connect as the owner role `opsgrids_admin`; the
# other two seed with `SET session_replication_role`, which only a superuser may do, so they get
# the superuser DSN. Each skips unless its OPSGRIDS_*_TEST_DATABASE_URL is set, and
# refuses (tests/infra/_helpers.py real_database_dsn) the shared `opsgrids` database or a
# non-local host. `tooling-db` creates the throwaway `opsgrids_tooling` (idempotent);
# `test-infra-db` also migrates it and runs the four modules. Defaults target the compose
# postgres; to use any other local cluster, say a throwaway on 5446:
#   make test-infra-db TOOLING_DB_PORT=5446 \
#     TOOLING_DB_PSQL="psql -h 127.0.0.1 -p 5446 -U opsgrids -d postgres" \
#     TOOLING_DB_INIT=tools/dev-bootstrap/init-db.sql
TOOLING_DB_HOST ?= localhost
TOOLING_DB_PORT ?= 5434
TOOLING_DB_PSQL ?= $(COMPOSE) exec -T postgres psql -U opsgrids -d opsgrids
TOOLING_DB_INIT ?=
TOOLING_DB_SUPER_URL = postgresql+asyncpg://opsgrids:opsgrids@$(TOOLING_DB_HOST):$(TOOLING_DB_PORT)/opsgrids_tooling
TOOLING_DB_ADMIN_URL = postgresql+asyncpg://opsgrids_admin:opsgrids@$(TOOLING_DB_HOST):$(TOOLING_DB_PORT)/opsgrids_tooling
TOOLING_DB_TESTS = tests/infra/test_purge_test_orgs_db.py tests/infra/test_purge_putaway_captures.py \
  tests/infra/test_putaway_docs.py tests/infra/test_runbook_putaway_coordinate_check.py

.PHONY: tooling-db
tooling-db: ## Create/grant the opsgrids_tooling database the tests/infra real-database tests target (idempotent, needs make infra)
	$(TOOLING_DB_PSQL) -v ON_ERROR_STOP=1 $(if $(TOOLING_DB_INIT),-v init_sql=$(TOOLING_DB_INIT)) -f - \
	  < tools/dev-bootstrap/tooling-db.sql

.PHONY: test-infra-db
test-infra-db: tooling-db ## Run the tests/infra purge + runbook tests against a migrated opsgrids_tooling (needs make infra)
	$(MAKE) migrate DATABASE_URL=$(TOOLING_DB_SUPER_URL)
	OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL=$(TOOLING_DB_ADMIN_URL) \
	OPSGRIDS_PURGE_TEST_DATABASE_URL=$(TOOLING_DB_SUPER_URL) \
	OPSGRIDS_RUNBOOK_TEST_DATABASE_URL=$(TOOLING_DB_SUPER_URL) \
	  uv run pytest -q -rs $(TOOLING_DB_TESTS)

# The one suite where nothing is stubbed: six services, an outbox relay and an event
# consumer, all started by the harness on ports the OS hands out, driven over real HTTP
# against real Postgres and RabbitMQ. It needs no free port reserved and no stack stopped —
# it allocates its own and generates the gateway's route table to match, so it can run
# alongside a stack you already have up. Ordered: the steps share state and read as one
# journey. `make infra` is the prerequisite this doesn't run for you; `e2e-db` it does, every
# time, because it's cheap and idempotent and a stale opsgrids_wt is a confusing way to fail.
#
# The one thing neither `e2e-db` nor `e2e` runs for you is the schema itself. The first time,
# and again whenever a migration is added, apply it with a make command-line variable —
# `make migrate DATABASE_URL=postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids_wt`
# — not a `DATABASE_URL=… make migrate` env-var prefix: this Makefile's `include .env` +
# `export` re-exports the name from `.env` after your prefix set it, so the prefix is silently
# overridden back to whatever `.env` points at.
.PHONY: e2e
e2e: e2e-db ## Boot the real services and drive one ordered journey over HTTP
	@echo "→ end-to-end (needs make infra, and the schema migrated into opsgrids_wt — see above)"
	uv run pytest e2e -q

.PHONY: check
check: lint typecheck test ## lint + typecheck + test — the fast path

# ---------------------------------------------------------------- the frontend gate
# One target per row, so a failure names the row, and the CI frontend and supply-chain jobs
# call exactly these. `-r` walks the pnpm workspace, so a new product app is covered the day it
# is added rather than when somebody remembers to list it here. `--no-bail` lets every package
# report before the row fails, the same reason the backend fan-out runs every service.
#
# Needs Node 22 and pnpm 11 on PATH, and `make frontend-install` once.
# `frontend/worker-app` is named explicitly: it is its own surface (ADR-0024 §7), not a
# `product-*`, so the glob does not already see it.
FRONTEND_PACKAGES := $(notdir $(wildcard frontend/opsgrids-* frontend/product-*)) worker-app

.PHONY: frontend-install
frontend-install: ## pnpm install from the lockfile, exactly (fails if package.json and the lock disagree)
	cd frontend && pnpm install --frozen-lockfile

.PHONY: frontend-typecheck
frontend-typecheck: ## tsc --noEmit, every app and package
	cd frontend && pnpm -r --no-bail typecheck

# ESLint through the one shared preset (opsgrids-config/eslint.preset.js): typescript-eslint,
# react-hooks, jsx-a11y, and the vitest guard against .only / .skip / .todo left in a test.
# `--max-warnings=0` in every package's script, so a warning is not a way past this row.
.PHONY: frontend-lint
frontend-lint: ## ESLint (incl. jsx-a11y and no focused/skipped tests) + unique dev/preview ports
	cd frontend && pnpm -r --no-bail lint
	uv run --no-project python tools/frontend_ports.py

# The frontend's `.importlinter`: which package may import which (ADR-0001), and no cycles.
# One cruise per package because each maps `@/` to its own src/ — see .dependency-cruiser.cjs.
.PHONY: frontend-boundaries
frontend-boundaries: ## dependency-cruiser: apps independent, shared packages via exports, no cycles
	@cd frontend && rc=0; for p in $(FRONTEND_PACKAGES); do \
	  [ -d "$$p/src" ] || continue; \
	  echo "→ $$p"; \
	  DEPCRUISE_PACKAGE=$$p pnpm exec depcruise --config .dependency-cruiser.cjs "$$p" || rc=1; \
	done; exit $$rc

# `--passWithNoTests` survives only where a package genuinely has no tests yet (opsgrids-internal),
# so a suite that stops being collected fails here instead of passing with zero.
# FRONTEND_FILTER narrows `frontend-test` and `frontend-build` to some packages, as pnpm
# selectors — `FRONTEND_FILTER="--filter ./opsgrids-ui --filter ./product-crm"`. CI uses it to run
# the workspace in four legs at once (.github/workflows/ci.yml, `frontend-tests`). Empty, the
# default, is every package: exactly what `make frontend-gate` and every local run have always done.
FRONTEND_FILTER ?=

.PHONY: frontend-test
frontend-test: ## vitest, every app and package (FRONTEND_FILTER narrows it)
	cd frontend && pnpm -r --no-bail $(FRONTEND_FILTER) test

# `build:buyer` is the storefront's second bundle — the one buyers actually load on a merchant's
# own domain (ADR-0012 §2) — and nothing built it before this row did. `--if-present` so any
# package that grows a second bundle the same way is covered by naming its script build:buyer.
.PHONY: frontend-build
frontend-build: ## Production builds, including the storefront's buyer bundle (FRONTEND_FILTER narrows it)
	cd frontend && pnpm -r $(FRONTEND_FILTER) build && pnpm -r --if-present $(FRONTEND_FILTER) build:buyer

# pnpm audit over the production tree, with an allow-list that expires (tools/npm_audit.py).
# Needs the network, like `make audit`.
.PHONY: frontend-audit
frontend-audit: ## Known high/critical vulnerabilities in what ships to a browser (pnpm audit --prod)
	uv run --no-project python tools/npm_audit.py

.PHONY: frontend-licenses
frontend-licenses: ## Every production npm package's licence is on the [npm] allow-list
	uv run --no-project python tools/licenses.py --npm

.PHONY: frontend-gate
frontend-gate: frontend-typecheck frontend-lint frontend-boundaries frontend-test frontend-build frontend-audit frontend-licenses ## Every frontend row

# Visual and accessibility regression (frontend/VISUAL-TESTING.md): builds the opsgrids-ui
# workshop and three apps, then screenshots and axe-checks every story and six real pages in both
# themes at 375/768/1280 against the committed baselines. Installs the pinned Chromium first —
# a no-op once it is there. Slow (minutes) and platform-bound (baselines are per OS), so it is a
# gate-full row, not a gate row.
.PHONY: ui-visual
ui-visual: ## Screenshots + axe over every opsgrids-ui story and six app pages, against the baselines
	cd frontend && pnpm --filter @opsgrids/visual run visual:browsers && pnpm run visual:check

# The name this had before the gate was split into rows. Kept so muscle memory still works.
.PHONY: frontend
frontend: frontend-gate ## Alias for frontend-gate

# ---------------------------------------------------------------- the rest of the gate
# Each of these is one row of the quality gate that used to be reported "N/A — no tooling".
# They are separate targets, and separate CI steps, so a failure names the row that failed.

# The lock file is what gets audited, not whatever happens to be installed: `uv export` is
# exactly what uv.lock resolves to, and --no-deps stops pip-audit re-resolving it. The export
# is fully pinned (`==` on every line), so --disable-pip is safe: without it pip-audit still
# builds a throwaway venv with pip in it just to read the file, which is slow and — on a
# uv-managed Python whose ensurepip aborts — fails the row for a reason unrelated to any
# vulnerability. This (and frontend-audit) are the targets that need the network.
.PHONY: audit
audit: ## Known vulnerabilities in the locked dependency set (pip-audit)
	uv export --frozen --no-hashes --no-emit-project --no-emit-workspace --color never \
	  --format requirements-txt \
	  | uv run pip-audit --no-deps --disable-pip --progress-spinner off -r /dev/stdin

.PHONY: licenses
licenses: ## Every dependency's licence is on the allow-list in tools/licenses.toml
	uv run python tools/licenses.py

# What the secret scan may ignore, and why. These are paths and line patterns, never
# detectors, so the scan stays as sensitive as it is — and a real credential pasted over
# one of these placeholders no longer matches the pattern, and is caught.
#
#   dev-only- / ci-only-       the development and CI placeholders (.env.example, the
#                              ServiceSettings defaults, ci.yml); production refuses them
#   opsgrids:opsgrids@             the bootstrap superuser DSN for the local stack
#   *_PASSWORD: opsgrids         compose and CI wiring for that same local stack
#   driver://user:pass@        Alembic's own placeholder URL; env.py overwrites it at runtime
#   frontend/pnpm-lock.yaml    integrity hashes look like secrets and are not
#
# Anything else that is a false positive — an event name containing "password", a test's
# fixture password — carries `# pragma: allowlist secret` on the line itself.
SECRETS_EXCLUDE_LINES := dev-only-|ci-only-|opsgrids:opsgrids@|(POSTGRES|MINIO_ROOT)_PASSWORD: "?opsgrids|PGPASSWORD=opsgrids|driver://user:pass@
SECRETS_EXCLUDE_FILES := frontend/pnpm-lock\.yaml$$

.PHONY: secrets
secrets: ## Secret scan over every tracked file (detect-secrets, against .secrets.baseline)
	git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline \
	  --exclude-files '$(SECRETS_EXCLUDE_FILES)' --exclude-lines '$(SECRETS_EXCLUDE_LINES)'

.PHONY: secrets-baseline
secrets-baseline: ## Regenerate .secrets.baseline with the exclusions above; audit before committing
	uv run detect-secrets scan \
	  --exclude-files '$(SECRETS_EXCLUDE_FILES)' --exclude-lines '$(SECRETS_EXCLUDE_LINES)' \
	  > .secrets.baseline

# Ruff already has rules for print (T20), breakpoint()/pdb (T10) and a bare `# noqa` or
# `# type: ignore` (PGH003/PGH004); they are selected here explicitly so this target is
# the whole answer for "is the diff clean". tools/hygiene.py covers what ruff has no rule
# for: skipped tests.
.PHONY: hygiene
hygiene: ## No skipped tests, debugger calls, print(), or bare noqa / type: ignore
	uv run ruff check --select T10,T20,PGH003,PGH004 .
	uv run python tools/hygiene.py

# Per service, like typecheck and test: each exposes a top-level `app`. The contracts are
# in .importlinter; --no-cache keeps a cache directory out of every service tree.
.PHONY: boundaries
boundaries: ## import-linter: the service layering and no import cycles
	@set -e; for s in $(SERVICES); do \
	  echo "→ $$s"; \
	  cfg=../../.importlinter; \
	  if [ -f ".importlinter.$$s" ]; then cfg=../../.importlinter.$$s; fi; \
	  (cd services/$$s && PYTHONPATH=. uv run lint-imports --config $$cfg --no-cache); \
	done

# The permission manifests ride with the OpenAPI targets rather than owning their own gate
# row: both are committed contracts generated from the code, both go stale the same way, and
# CI already runs `make openapi-check` — one habit covers both.
.PHONY: openapi
openapi: ## Regenerate libs/contracts/{openapi,permissions}/*.json from the code
	uv run python tools/export_openapi.py
	uv run python tools/export_permissions.py

.PHONY: openapi-check
openapi-check: ## Fail if a committed OpenAPI document or permission manifest is behind the code
	uv run python tools/export_openapi.py --check
	uv run python tools/export_permissions.py --check

.PHONY: contracts-check
contracts-check: ## Every event schema is valid and covers what its service actually emits
	uv run pytest libs/contracts -q

# ---------------------------------------------------------------- shell scripts and Dockerfiles
# Both run the pinned version and only the pinned version: a local binary is used when it is
# exactly that version, otherwise the pinned image. A linter whose rules change with whatever
# the laptop or the runner happens to have installed gives two answers for one commit —
# ubuntu-latest ships an older shellcheck than Homebrew, and they disagree.
#
# Install locally with `brew install shellcheck hadolint`; without them, Docker is enough.
SHELLCHECK_VERSION := 0.11.0
HADOLINT_VERSION   := 2.15.1

# Every tracked *.sh, plus the service entrypoints (services/*/bin/*) that are shell by shebang.
#
# `-S style` — every severity, the strictest there is. There are ~150 scripts and the deploy
# path runs as root on the production box: the cost of reading one more note is lower than the
# cost of the `A && B || C` that runs C after B succeeded (SC2015, found and fixed in
# preflight.sh when this row was added). A finding that is deliberate is disabled on its own
# line with `# shellcheck disable=SCxxxx # reason`, never here. `-x` follows `source`, so
# remote.sh is checked together with the lib/ it loads.
.PHONY: shellcheck
shellcheck: ## shellcheck (pinned) over every shell script, every severity
	@files=$$( { git ls-files '*.sh'; \
	  git ls-files 'services/*/bin/*' | xargs awk 'FNR == 1 && /^#!.*[\/ ](ba|da)?sh( |$$)/ { print FILENAME }'; } \
	  | sort -u ); \
	args="-x --source-path=SCRIPTDIR -S style"; \
	echo "→ $$(echo "$$files" | wc -l | tr -d ' ') scripts"; \
	if shellcheck --version 2>/dev/null | grep -qx 'version: $(SHELLCHECK_VERSION)'; then \
	  shellcheck $$args $$files; \
	else \
	  echo "  (shellcheck $(SHELLCHECK_VERSION) not on PATH — using koalaman/shellcheck:v$(SHELLCHECK_VERSION))"; \
	  docker run --rm -v "$(CURDIR):/mnt:ro" -w /mnt koalaman/shellcheck:v$(SHELLCHECK_VERSION) $$args $$files; \
	fi

# Every tracked Dockerfile. The threshold and the reasoned ignores are in .hadolint.yaml.
.PHONY: hadolint
hadolint: ## hadolint (pinned) over every Dockerfile
	@files=$$(git ls-files '*Dockerfile' '*.Dockerfile'); \
	echo "→ $$(echo "$$files" | wc -l | tr -d ' ') Dockerfiles"; \
	if hadolint --version 2>/dev/null | grep -q ' $(HADOLINT_VERSION)$$'; then \
	  hadolint $$files; \
	else \
	  echo "  (hadolint $(HADOLINT_VERSION) not on PATH — using hadolint/hadolint:v$(HADOLINT_VERSION))"; \
	  docker run --rm -v "$(CURDIR):/mnt:ro" -w /mnt hadolint/hadolint:v$(HADOLINT_VERSION) hadolint $$files; \
	fi

# ---------------------------------------------------------------- the gate
# `gate` is every row that runs against a database you already have (`make infra` + migrated),
# in the order that fails cheapest first. It is the union of what the CI jobs run — every CI
# step calls one of these targets — so a green `make gate` is the same claim as a green CI run,
# and with GitHub Actions unavailable it is the only one there is.
#
# The static rows need nothing but the toolchain; the database rows are last:
#   migration-roundtrip  upgrade → downgrade → upgrade, every service (CI: migrations shards)
#   migration-check      models match migrations; needs the schemas at head, which the
#                        roundtrip leaves them at — so it must stay after it (CI: same shard,
#                        same order)
#   smoke                the real entrypoint's /health and /health/ready
#
# Not parallel-safe as a whole (`make -j gate`): migration-check depends on the roundtrip's end
# state. Each row parallelises internally through JOBS instead.
GATE_STATIC := lint hygiene boundaries typecheck test-infra shard-verify test-shard-verify openapi-check \
               contracts-check shellcheck hadolint audit licenses secrets frontend-gate
GATE_DB     := test-libs test-services migration-roundtrip migration-check smoke

.PHONY: gate
gate: SHARDS = 3
gate: $(GATE_STATIC) $(GATE_DB) ## The whole quality gate: every row CI runs, run locally
	@echo "gate: every row passed"

# `gate` plus the rows too slow or too stateful for every change. Later rows land here, not in
# `gate`, each as its own target:
#   e2e        the real services over HTTP (needs `make infra` + opsgrids_wt migrated; see e2e)
#   ui-visual  screenshots + axe over every workshop story and six app pages (see ui-visual)
#   images     container image scan                       (extension point — not yet a target)
GATE_FULL_EXTRA := e2e ui-visual

.PHONY: gate-full
gate-full: gate $(GATE_FULL_EXTRA) ## gate + the slow rows (end-to-end, visual + a11y regression; images later)
	@echo "gate-full: every row passed"

# ---------------------------------------------------------------- the edge (ADR-0012)
# One Linux VM, Docker Compose and Caddy. Deploys are `ssh` from a laptop with the engineer's
# own key — no registry, no CI deploy job, no GitHub secret (ADR-0012 §1). That is a
# deliberate simplification and it makes "deployed by accident" the risk to design against, so
# every target below:
#
#   * requires HOST= — an ssh target you type. There is no default and no fallback;
#   * requires CONFIRM= — which must equal the EDGE_HOST the box records in its own .env, so
#     aiming at the wrong machine fails on the first ssh, before anything is fetched or built;
#   * deploys a pushed REF, never your working tree.
#
# Everything these targets do on the box is in infrastructure/edge/remote.sh, which ships with
# the commit being deployed. Read infrastructure/edge/RUNBOOK.md before the first one.
EDGE_REPO ?= /srv/opsgrids/repo
REF       ?= origin/main
EDGE_SSH   = ssh -o BatchMode=yes $(HOST) $(EDGE_REPO)/infrastructure/edge/remote.sh

.PHONY: edge-guard
edge-guard:
	@test -n "$(HOST)" || (echo "HOST= is required. Name the box: HOST=deploy@203.0.113.7" && exit 1)
	@test -n "$(CONFIRM)" || (echo "CONFIRM= is required. It must match the box's EDGE_HOST: CONFIRM=edge.opsgrids.com" && exit 1)
	@$(EDGE_SSH) assert-host $(CONFIRM)

.PHONY: deploy
deploy: ## Deploy the edge: make deploy HOST=deploy@host CONFIRM=edge.opsgrids.com [REF=origin/main]
	# preflight → build → caddy validate → migrate → up → health, in that order, and the
	# first three change nothing. A build that fails leaves the running site untouched; the
	# recreate at `up` is a few seconds of downtime, not zero. See the runbook.
	@test -n "$(HOST)" || (echo "HOST= is required. Name the box: HOST=deploy@203.0.113.7" && exit 1)
	@test -n "$(CONFIRM)" || (echo "CONFIRM= is required. It must match the box's EDGE_HOST" && exit 1)
	EDGE_REPO=$(EDGE_REPO) infrastructure/edge/ssh-deploy.sh $(HOST) $(CONFIRM) $(REF) deploy

.PHONY: edge-rollback
edge-rollback: edge-guard ## Redeploy the previous good ref: make edge-rollback HOST=… CONFIRM=… [TO=<sha>]
	@ref="$(TO)"; \
	 if [ -z "$$ref" ]; then ref=$$($(EDGE_SSH) previous-ref); fi; \
	 echo "rolling back to $$ref"; \
	 EDGE_REPO=$(EDGE_REPO) infrastructure/edge/ssh-deploy.sh $(HOST) $(CONFIRM) "$$ref" deploy

.PHONY: edge-reconcile
edge-reconcile: edge-guard ## Widen existing orgs' roles with new permission codes: HOST=… CONFIRM=… PRODUCTS=crm,finance [APPLY=1]
	@test -n "$(PRODUCTS)" || (echo "PRODUCTS= is required: a comma-separated list of product keys" && exit 1)
	@$(EDGE_SSH) reconcile-permissions $(PRODUCTS) $(if $(APPLY),--apply,)

.PHONY: edge-preflight
edge-preflight: edge-guard ## Check the box is deployable without changing anything: HOST=… CONFIRM=…
	@$(EDGE_SSH) preflight

.PHONY: edge-validate
edge-validate: ## Load the Caddyfile at REF with the pinned Caddy, restart nothing: HOST=… CONFIRM=…
	@test -n "$(HOST)" || (echo "HOST= is required" && exit 1)
	@test -n "$(CONFIRM)" || (echo "CONFIRM= is required" && exit 1)
	EDGE_REPO=$(EDGE_REPO) infrastructure/edge/ssh-deploy.sh $(HOST) $(CONFIRM) $(REF) validate

.PHONY: edge-status
edge-status: edge-guard ## What is running, which ref, how much disk: HOST=… CONFIRM=…
	@$(EDGE_SSH) status

.PHONY: edge-logs
edge-logs: edge-guard ## Tail the edge logs: make edge-logs HOST=… CONFIRM=… [SERVICE=caddy] [TAIL=200]
	@$(EDGE_SSH) logs $(or $(TAIL),200) $(SERVICE)

.PHONY: edge-backup
edge-backup: edge-guard ## Run the nightly backup now (dump + certificates, off-box): HOST=… CONFIRM=…
	@$(EDGE_SSH) backup

# AGE_KEY is a path on THIS machine: the private key goes over ssh's stdin into one shell variable
# on the box and never onto its disk. Omit it only for a bucket that still holds plaintext dumps.
.PHONY: edge-restore-drill
edge-restore-drill: edge-guard ## Restore the newest off-box dump into opsgrids_restore, compare row counts, drop it: HOST=… CONFIRM=… [AGE_KEY=~/age-key.txt]
	@$(EDGE_SSH) restore-drill < $(or $(AGE_KEY),/dev/null)

# The buyer bundle is built here and shipped, not built on the box: the root .dockerignore
# keeps `frontend` out of every image build context, and shipping only a bundle that compiled
# is what stops a failed frontend build from breaking a merchant's shop.
.PHONY: edge-web
edge-web: ## Build and publish a bundle: make edge-web HOST=… CONFIRM=… [APP=buyer|dashboard|operations|<product key>] [PLACEHOLDER=1]
	@test -n "$(HOST)" || (echo "HOST= is required" && exit 1)
	@test -n "$(CONFIRM)" || (echo "CONFIRM= is required" && exit 1)
	EDGE_REPO=$(EDGE_REPO) APP=$(or $(APP),buyer) PLACEHOLDER=$(PLACEHOLDER) \
	  infrastructure/edge/publish-web.sh publish $(HOST) $(CONFIRM)

.PHONY: edge-web-rollback
edge-web-rollback: ## Point the edge back at the previous bundle (one symlink): HOST=… CONFIRM=…
	@test -n "$(HOST)" || (echo "HOST= is required" && exit 1)
	@test -n "$(CONFIRM)" || (echo "CONFIRM= is required" && exit 1)
	EDGE_REPO=$(EDGE_REPO) APP=$(or $(APP),buyer) infrastructure/edge/publish-web.sh rollback $(HOST) $(CONFIRM)

.PHONY: edge-releases
edge-releases: ## Which bundles are on the box and which one is live: HOST=… CONFIRM=…
	@test -n "$(HOST)" || (echo "HOST= is required" && exit 1)
	@test -n "$(CONFIRM)" || (echo "CONFIRM= is required" && exit 1)
	EDGE_REPO=$(EDGE_REPO) APP=$(or $(APP),buyer) infrastructure/edge/publish-web.sh releases $(HOST) $(CONFIRM)
