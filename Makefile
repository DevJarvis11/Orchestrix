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

.PHONY: help env dev dev-full down web logs ps migrate seed test test-integration lint format \
	typecheck gateway-check opa-test gitleaks docker-build web-check ci dev-token test-resilience test-stack record-fixtures obs demo scan eval-smoke eval-full eval-calibrate

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

env: ## Create .env with random secrets (never overwrites)
	@scripts/gen-env.sh

dev: env ## Start the core profile and wait until every service is healthy
	$(COMPOSE) --profile core up -d --build --wait

dev-full: env ## Core plus observability and auth profiles (memory-heavy)
	ORCHESTRIX_LANGFUSE_HOST=http://langfuse-web:3000 \
	$(COMPOSE) --profile core --profile observability --profile auth up -d --build --wait

obs: env ## Core plus the observability profile (Langfuse, Prometheus, Grafana); tracing on
	ORCHESTRIX_LANGFUSE_HOST=http://langfuse-web:3000 \
	$(COMPOSE) --profile core --profile observability up -d --build --wait

down: ## Stop the stack (volumes kept)
	$(COMPOSE) --profile core --profile observability --profile auth down

web: ## UI dev server on :5173; reachable from a Windows browser at the WSL IP (API proxied by Vite)
	@echo "Open http://localhost:5173 or, from a Windows browser, http://$$(hostname -I | awk '{print $$1}'):5173"
	cd apps/web && VITE_API_URL="" pnpm dev --host 0.0.0.0

logs: ## Follow logs (SERVICE=name to filter)
	$(COMPOSE) --profile core --profile observability --profile auth logs -f $(SERVICE)

ps: ## Service status
	$(COMPOSE) --profile core ps

migrate: env ## Alembic upgrade head + checkpointer setup (one-shot container)
	$(COMPOSE) --profile core run --rm --build migrate

seed: ## Seed demo users (two teams), the showcase lesson memory and demo_data tables
	uv run python scripts/seed.py

demo: env ## SPEC 5.6 with recorded web fixtures, seeded data and scripted approvals (MODEL= to pin one)
	ORCHESTRIX_WEB_FIXTURES_DIR=/fixtures/web ORCHESTRIX_WEB_FIXTURES_ONLY=true \
	$(COMPOSE) --profile core up -d --build --wait
	$(MAKE) --no-print-directory seed
	uv run python scripts/showcase.py --model "$${MODEL:-}"  # MODEL= any catalogue model; empty = configured defaults

dev-token: ## Provision a demo user and team; print a dev JWT (EMAIL=, TEAM=, ROLES=)
	@uv run python -m orchestrix_api.security.dev_jwt --email $${EMAIL:-demo@orchestrix.local} --team $${TEAM:-demo} --roles $${ROLES:-requester,reviewer,operator,admin}

test-resilience: env ## NFR-01: SIGKILL the worker holding a run; another worker finishes it
	ORCHESTRIX_FAKE_MODELS=true ORCHESTRIX_FAKE_MODEL_DELAY_SECONDS=6 \
	ORCHESTRIX_LEASE_SECONDS=10 ORCHESTRIX_HEARTBEAT_SECONDS=3 \
	$(COMPOSE) --profile core up -d --build --wait --scale worker=2
	uv run pytest -m resilience tests/e2e -v || (status=$$?; $(MAKE) --no-print-directory dev; exit $$status)
	$(MAKE) --no-print-directory dev  # back to normal settings and one worker

test-stack: ## Live checks against `make dev`: sandbox isolation (FR-26), MCP servers
	uv run pytest -m stack tests/e2e -v

record-fixtures: ## Record scrubbed web and model fixtures (requires running SearXNG and LiteLLM)
	uv run python scripts/record_fixtures.py

test: ## Unit and graph tests (no Docker)
	uv run pytest -m "not integration and not resilience and not stack"

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

IMAGES := orchestrix-api:dev orchestrix-worker:dev orchestrix-mcp-web:dev orchestrix-mcp-sandbox:dev \
	orchestrix-mcp-data:dev orchestrix-mcp-files:dev orchestrix-mcp-comms:dev

scan: docker-build gitleaks ## Trivy (images + lockfiles; fail on fixable HIGH/CRITICAL) and gitleaks
	@for image in $(IMAGES); do \
		docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache \
			$(TRIVY_IMAGE) image --quiet --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $$image || exit 1; \
	done
	docker run --rm -v "$(CURDIR):/repo:ro" -v trivy-cache:/root/.cache $(TRIVY_IMAGE) fs --quiet \
		--scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 /repo

eval-smoke: ## Eval smoke: first 10 golden tasks x 3 runs (needs the stack; MODELS=, JUDGE=)
	uv run python -m orchestrix_evals run --suite smoke --models $${MODELS:-default} $${JUDGE:+--judge-models $$JUDGE} --out reports/eval-smoke

eval-full: ## All datasets x 3 runs in the background (hours locally); log in reports/eval-full.log
	mkdir -p reports
	nohup sh -c 'for s in full defects escalation injection memory; do uv run python -m orchestrix_evals run --suite $$s --models $${MODELS:-default} $${JUDGE:+--judge-models $$JUDGE} --out reports/eval-$$s; done' > reports/eval-full.log 2>&1 &
	@echo "eval-full started in the background: tail -f reports/eval-full.log"

eval-calibrate: ## Judge-vs-human kappa (DIR= holding outputs.jsonl, human_a.jsonl, human_b.jsonl)
	uv run python -m orchestrix_evals calibrate --outputs $${DIR:-reports/calibration}/outputs.jsonl --human-a $${DIR:-reports/calibration}/human_a.jsonl --human-b $${DIR:-reports/calibration}/human_b.jsonl $${JUDGE:+--judge-models $$JUDGE}

docker-build: ## Build every service image
	$(COMPOSE) --profile core build

web-check: ## Frontend build
	pnpm --filter @orchestrix/web build

ci: lint typecheck test test-integration opa-test web-check docker-build gitleaks ## Everything ci.yml runs
	@echo "make ci: all checks passed"
