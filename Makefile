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
test: test-libs test-services ## Everything CI runs

.PHONY: test-libs
test-libs: ## Shared library tests
	uv run pytest libs

.PHONY: test-shard
test-shard: ## Tests for one shard of services: make test-shard SHARD=1 SHARDS=3
	# CI splits the service tests across jobs rather than across processes, because on a
	# 2-vCPU runner a second process makes both slower (see .github/workflows/ci.yml). Each
	# shard gets its own runner and its own database, so the parallelism is real capacity.
	@test -n "$(SHARD)" || (echo "SHARD= is required" && exit 1)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	@tools/shard-verify.sh $(SHARDS)
	@tools/shard.sh $(SHARD) $(SHARDS) \
	  | tools/fan-out.sh --stdin $(JOBS) tests ok-empty uv run pytest tests -q -p no:cacheprovider

.PHONY: migration-check-shard
migration-check-shard: ## Model/migration drift for one shard: SHARD=1 SHARDS=3
	@test -n "$(SHARD)" || (echo "SHARD= is required" && exit 1)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	@tools/shard.sh $(SHARD) $(SHARDS) \
	  | tools/fan-out.sh --stdin $(JOBS) alembic.ini strict uv run alembic check

.PHONY: migration-shard
migration-shard: ## Migration round-trip for one shard: make migration-shard SHARD=1 SHARDS=3
	@test -n "$(SHARD)" || (echo "SHARD= is required" && exit 1)
	@test -n "$(SHARDS)" || (echo "SHARDS= is required" && exit 1)
	@tools/shard-verify.sh $(SHARDS)
	@tools/shard.sh $(SHARD) $(SHARDS) \
	  | tools/fan-out.sh --stdin $(JOBS) alembic.ini strict $(CURDIR)/tools/roundtrip.sh

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

# The frontend's equivalent of `check`, and exactly what the CI frontend job runs. `-r` walks
# the pnpm workspace, so a new product app is covered the day it is added rather than when
# somebody remembers to list it here. Needs `pnpm install` in frontend/ once.
.PHONY: frontend
frontend: ## Frontend: typecheck + lint + test + build, every app and package
	cd frontend && pnpm -r typecheck && pnpm -r lint && pnpm -r test && pnpm -r build

# ---------------------------------------------------------------- the rest of the gate
# Each of these is one row of the quality gate that used to be reported "N/A — no tooling".
# They are separate targets, and separate CI steps, so a failure names the row that failed.

# The lock file is what gets audited, not whatever happens to be installed: `uv export` is
# exactly what uv.lock resolves to, and --no-deps stops pip-audit re-resolving it. This is
# the one target that needs the network, for the advisory database.
.PHONY: audit
audit: ## Known vulnerabilities in the locked dependency set (pip-audit)
	uv export --frozen --no-hashes --no-emit-project --no-emit-workspace --color never \
	  --format requirements-txt \
	  | uv run pip-audit --no-deps --progress-spinner off -r /dev/stdin

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
	  (cd services/$$s && PYTHONPATH=. uv run lint-imports --config ../../.importlinter --no-cache); \
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

.PHONY: gate
gate: check audit licenses secrets hygiene boundaries openapi-check migration-roundtrip smoke ## The whole quality gate — everything CI runs
