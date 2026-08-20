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
typecheck: typecheck-libs typecheck-services ## mypy over libs and every service

.PHONY: typecheck-libs
typecheck-libs: ## mypy over the shared libraries
	uv run mypy libs

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

.PHONY: check
check: lint typecheck test ## What CI runs
