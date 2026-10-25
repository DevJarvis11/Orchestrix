# Orchestrix developer commands. `make ci` runs exactly what .github/workflows/ci.yml runs.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Containers must reach the WSL Ollama by the distro's IP: host.docker.internal resolves to the
# Windows host (a different, empty Ollama). The IP can change across WSL restarts, so derive it.
# A value in the shell environment wins; it overrides .env for Compose.
WSL_IP := $(shell hostname -I | awk '{print $$1}')
export OLLAMA_BASE_URL ?= http://$(WSL_IP):11434

COMPOSE := docker compose --env-file .env -f infra/compose/docker-compose.yml
OPA_IMAGE := openpolicyagent/opa:1.21.3
GITLEAKS_IMAGE := zricethezav/gitleaks:v8.30.1
TRIVY_IMAGE := aquasec/trivy:0.76.1

.PHONY: help env dev dev-full down logs ps migrate seed test test-integration lint format \
	typecheck gateway-check opa-test gitleaks docker-build web-check ci

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

env: ## Create .env with random secrets (never overwrites)
	@scripts/gen-env.sh

dev: env ## Start the core profile and wait until every service is healthy
	$(COMPOSE) --profile core up -d --build --wait

dev-full: env ## Core plus observability and auth profiles (memory-heavy)
	$(COMPOSE) --profile core --profile observability --profile auth up -d --build --wait

down: ## Stop the stack (volumes kept)
	$(COMPOSE) --profile core --profile observability --profile auth down

logs: ## Follow logs (SERVICE=name to filter)
	$(COMPOSE) --profile core logs -f $(SERVICE)

ps: ## Service status
	$(COMPOSE) --profile core ps

migrate: env ## Alembic upgrade head + checkpointer setup (one-shot container)
	$(COMPOSE) --profile core run --rm --build migrate

seed: ## Seed demo users, memories and data (filled in M8)
	@echo "seed: nothing to seed until M8"

test: ## Unit and graph tests (no Docker)
	uv run pytest -m "not integration"

test-integration: ## Integration tests (testcontainers; needs Docker)
	uv run pytest -m integration

lint: ## ruff + Biome
	uv run ruff check .
	uv run ruff format --check .
	pnpm --filter @orchestrix/web lint

format: ## Auto-format Python and TypeScript
	uv run ruff check --fix .
	uv run ruff format .
	pnpm --filter @orchestrix/web format

typecheck: ## pyright (strict on apps/api and packages/*) + tsc
	uv run pyright
	pnpm --filter @orchestrix/web typecheck

gateway-check: ## One chat, json_schema and tool call per model, an embedding and a forced fallback (ARGS=--local-only)
	uv run python -m orchestrix_core.gateway_check $(ARGS)

opa-test: ## Rego unit tests (policies arrive in M4)
	@if compgen -G "policies/*.rego" > /dev/null; then \
		docker run --rm -v "$(CURDIR)/policies:/policies:ro" $(OPA_IMAGE) test /policies -v; \
	else echo "opa-test: no policies yet"; fi

gitleaks: ## Secret scan of git history and the working tree
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) git /repo --config /repo/.gitleaks.toml --redact
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) dir /repo --config /repo/.gitleaks.toml --redact

docker-build: ## Build every service image
	$(COMPOSE) --profile core build

web-check: ## Frontend build
	pnpm --filter @orchestrix/web build

ci: lint typecheck test test-integration opa-test web-check docker-build gitleaks ## Everything ci.yml runs
	@echo "make ci: all checks passed"
