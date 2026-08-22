# Atmora Platform — developer entry points.
.DEFAULT_GOAL := help
SHELL := /bin/bash

# Services are discovered, not listed: a new service directory is picked up with no edit here.
SERVICES := $(notdir $(wildcard services/*))
COMPOSE  := docker compose -f tools/dev-bootstrap/compose.yml --env-file .env

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
	@set -e; for s in $(SERVICES); do \
	  test -f services/$$s/alembic.ini || continue; \
	  echo "→ $$s"; (cd services/$$s && uv run alembic upgrade head); \
	done

.PHONY: makemigration
makemigration: ## Autogenerate: make makemigration SERVICE=account M="add memberships"
	@test -n "$(SERVICE)" || (echo "SERVICE= is required" && exit 1)
	@test -n "$(M)" || (echo 'M="message" is required' && exit 1)
	cd services/$(SERVICE) && uv run alembic revision --autogenerate -m "$(M)"

.PHONY: migration-check
migration-check: ## Fail if a model changed without a migration
	@set -e; for s in $(SERVICES); do \
	  test -f services/$$s/alembic.ini || continue; \
	  echo "→ $$s"; (cd services/$$s && uv run alembic check); \
	done

# A downgrade() nobody has run is a rollback nobody has. Going to base and back proves each
# one actually reverts what its upgrade() did — against the configured database, so run it
# where you would run `make migrate`. env.py re-applies the schema grants after every step.
.PHONY: migration-roundtrip
migration-roundtrip: ## upgrade head → downgrade base → upgrade head, every service
	@set -e; for s in $(SERVICES); do \
	  test -f services/$$s/alembic.ini || continue; \
	  echo "→ $$s"; (cd services/$$s && uv run alembic upgrade head \
	    && uv run alembic downgrade base && uv run alembic upgrade head); \
	done

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
	@set -e; for s in $(SERVICES); do \
	  echo "→ $$s"; (cd services/$$s && uv run mypy app); \
	done

.PHONY: test
test: test-libs test-services ## Everything CI runs

.PHONY: test-libs
test-libs: ## Shared library tests
	uv run pytest libs

.PHONY: test-services
test-services: ## Each service in its own process
	@set -e; for s in $(SERVICES); do \
	  echo "→ $$s"; rc=0; \
	  (cd services/$$s && uv run pytest tests -p no:cacheprovider) || rc=$$?; \
	  if [ $$rc -eq 5 ]; then echo "  (no tests yet)"; elif [ $$rc -ne 0 ]; then exit $$rc; fi; \
	done

.PHONY: e2e
e2e: ## Drive the running platform over HTTP — starts the services itself
	@echo "→ end-to-end (needs make infra, and ports 8100/8110/8111 free)"
	uv run pytest e2e -q

.PHONY: check
check: lint typecheck test ## lint + typecheck + test — the fast path

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
#   atmora:atmora@             the bootstrap superuser DSN for the local stack
#   *_PASSWORD: atmora         compose and CI wiring for that same local stack
#   driver://user:pass@        Alembic's own placeholder URL; env.py overwrites it at runtime
#   frontend/pnpm-lock.yaml    integrity hashes look like secrets and are not
#
# Anything else that is a false positive — an event name containing "password", a test's
# fixture password — carries `# pragma: allowlist secret` on the line itself.
SECRETS_EXCLUDE_LINES := dev-only-|ci-only-|atmora:atmora@|(POSTGRES|MINIO_ROOT)_PASSWORD: "?atmora|PGPASSWORD=atmora|driver://user:pass@
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

.PHONY: openapi
openapi: ## Regenerate libs/contracts/openapi/<service>.json from the code
	uv run python tools/export_openapi.py

.PHONY: openapi-check
openapi-check: ## Fail if a committed OpenAPI document is behind the code
	uv run python tools/export_openapi.py --check

.PHONY: gate
gate: check audit licenses secrets hygiene boundaries openapi-check migration-roundtrip smoke ## The whole quality gate — everything CI runs
