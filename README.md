# Orchestrix

Orchestrix turns one complex request into a planned, executed, reviewed and human-approved deliverable, with an audit trail of every agent decision. A supervisor plans a DAG of subtasks. Specialist agents run the subtasks in parallel with tools behind MCP, a reviewer scores every result, people approve risky steps, and long-term memory carries lessons into future plans. Every run is checkpointed, traced, cost-accounted and replayable.

`SPEC.md` is the source of truth. `docs/IMPLEMENTATION_PLAN.md` is the approved local build plan, and `docs/BUILD_LOG.md` records status, versions, decisions and deviations.

## How it fits together

```text
browser (React SPA) ──HTTPS──▶ API (FastAPI) ──▶ Postgres (app tables, checkpoints, memories, events)
                                   │  enqueue          ▲
                                   ▼                   │ checkpoints, rows
                               Redis (jobs, pub/sub) ──▶ Worker (LangGraph run graph, under a lease)
                                                          │        │          │
                                                 LiteLLM proxy   OPA      MCP servers
                                               (gpt-oss-120b,  (policy)  web · sandbox · data · files · comms
                                                qwen3 fallback)
                                   OpenTelemetry ─▶ Langfuse      Prometheus ─▶ Grafana
```

- **API** (`apps/api`): auth, validation, task intake, SSE, escalation decisions, memory, costs, traces, replay, admin. It is stateless.
- **Worker** (`apps/worker`): runs graph steps for runs it holds a Postgres lease on. A killed worker's run is reclaimed and resumed from its last checkpoint by another worker (NFR-01).
- **Graph** (`packages/agents`): intake → retrieve memory → plan (optionally clarify) → validate → plan gate → dispatch ⇄ subtask workers (execute → review → revise or escalate) → synthesize → final review → final gate → write memory → finalize. Every human decision is an `interrupt()`, and every resume is `Command(resume={interrupt_id: decision})`.
- **Specialists** (`research`, `data_analyst`, `writer`, `coder`) are LangChain `create_agent` loops. Their middleware enforces the budget, tool allow-lists, the OPA policy (with approval pauses before sensitive tools), injection scanning with taint tracking, per-call accounting and retries.
- **Tools** live in five MCP servers, each in its own container, behind a service token. The sandbox has no network and no storage credentials.
- **Memory** (`packages/memory`): hybrid vector and keyword retrieval with RRF, reranking, decay and nightly consolidation. Deletion cascades into summaries.

## Diagrams

Interactive diagrams (open in a browser; sources are the `.json` files beside them, rendered with Archify):
[architecture](docs/diagrams/architecture.html), [run graph](docs/diagrams/run-graph.html), [human approval sequence](docs/diagrams/approval-sequence.html), [task lifecycle](docs/diagrams/task-lifecycle.html).

## Run it

The full step-by-step guide, including how to switch models and troubleshoot, is [docs/RUN_LOCALLY.md](docs/RUN_LOCALLY.md). The short version:

Requirements: WSL Ubuntu 22.04, Docker, uv, Node 26 with pnpm. Copy nothing by hand: `make env` writes `.env` with random secrets.

If `make dev` fails with `docker-credential-desktop.exe: exec format error`, run `echo '{}' > ~/.docker/config.json` (the Windows credential helper cannot run in WSL; only public images are pulled). If `make seed` fails with `server closed the connection`, wait until `make dev` reports healthy and run it again.

First time only:

```bash
uv sync && pnpm install   # Python and Node dependencies
make dev                  # start the core stack and wait until healthy
make migrate              # Alembic + LangGraph checkpointer tables
make seed                 # demo users and logins (admin, alice, bob), the showcase lesson memory, demo_data tables
```

Every time:

```bash
make dev                  # core stack: Postgres, Redis, S3, OPA, LiteLLM, API, worker, scheduler, MCP servers, SearXNG
make web                  # the UI on port 5173 (keep this terminal open; Ctrl+C stops it)
```

Open the UI at **http://localhost:5173** and sign in (dev mode, passwords are stored hashed in Postgres by `make seed`):

| Username | Password | Roles |
|---|---|---|
| `admin` | `admin` | requester, reviewer, operator, admin |
| `alice` | `alice@123` | requester (platform team) |
| `bob` | `bob@123` | requester, reviewer (platform team) |

To try approvals, submit as alice and approve as bob: a requester cannot approve their own escalation. For curl, `make dev-token` still prints a JWT (EMAIL=, TEAM=, ROLES=).

To see tasks in the UI, run `make demo` (recorded fixtures, no web access) or, with the VPN on, `uv run python scripts/showcase.py`.

 From a Windows browser, use the WSL address that `make web` prints, for example `http://192.168.117.53:5173`. WSL in its default NAT mode does not forward Windows `localhost`, and that address changes after a restart (`hostname -I` shows it). The UI calls the API through the dev server, so only port 5173 has to be reachable.

Stop everything:

```bash
# Ctrl+C in the make web terminal (or `fuser -k 5173/tcp`), then:
make down                 # stop every container (data volumes are kept)
```

For a live run on Yotta `gpt-oss-120b`, switch the VPN on first. Without it, tasks fall back to local `qwen3:8b` when `ORCHESTRIX_LOCAL_FALLBACK=true`.

Profiles: `make obs` adds Langfuse (http://localhost:3100), Prometheus and Grafana (http://localhost:3101), and switches tracing on. `make dev-full` also adds Keycloak (http://localhost:8180). To use Keycloak logins instead of the password form, set `ORCHESTRIX_AUTH_MODE=oidc`. The demo users are alice, bob, carol, dave, oscar and ada, and their password is `KC_DEMO_PASSWORD` in `.env`.

Models: the app is model-agnostic. Agents ask for a role, `ORCHESTRIX_DEFAULT_MODELS` in `.env` maps each role to a model, and `infra/litellm/config.yaml` defines the models (provider and price). The defaults are Yotta `gpt-oss-120b` for chat, `qwen3-embed` and `qwen3-rerank` over the VPN. `ORCHESTRIX_LOCAL_FALLBACK=true` adds a host-Ollama fallback. `make gateway-check` probes every model in the catalogue.

The showcase (SPEC 5.6) runs offline: `make demo` uses local models and recorded web fixtures, and scripts the approvals.

## Checks

| Command | What it runs |
|---|---|
| `make ci` | ruff, Biome, pyright (strict on apps/api and packages), tsc, unit and graph tests, integration tests (testcontainers), `opa test`, the frontend build, Docker builds, gitleaks |
| `make test-stack` | live sandbox isolation (no network, read-only root, time limit) and the MCP servers |
| `make test-resilience` | SIGKILL the worker mid-run; another worker finishes it with no duplicated side effects |
| `make scan` | Trivy over images and lockfiles, plus gitleaks |
| `make eval-smoke` / `make eval-full` | the eval suite (SPEC 20.2); needs the VPN for the headline numbers |

## How to add a specialist

1. Add its name to the `Specialist` literal in `packages/core/src/orchestrix_core/schemas/plan.py`.
2. Add a prompt `packages/agents/src/orchestrix_agents/prompts/specialists/<name>.j2` that includes `_rules.j2`.
3. Give it tools: add the name to `allowed_specialists` of each tool in `packages/tools/src/orchestrix_tools/catalogue.py`. Admins can change this later in the tools registry.
4. Optionally add a time limit in `RunContext.specialist_seconds`. No graph changes are needed: the planner sees the new specialist and its tools.

## How to add a tool

1. Implement it in an MCP server under `mcp_servers/<server>/`, using `new_server()`, `build_app()` and `tool_error()` from `orchestrix_tools.conventions`. Validate inputs with Pydantic, put a hard timeout on the call, and return `{error_code, message, retryable}` on errors.
2. Add a `ToolSpec` to the catalogue: `sensitive`, `side_effecting`, rate limit, and allowed specialists. On start, the worker loads its schema, syncs it into the `tools` table and pushes the registry to OPA.
3. If it needs a new policy rule, add it to `policies/tool.rego` together with an `opa test` case.

## Evaluation

Numbers come from `packages/evals` (SPEC 20.2), reported as means with bootstrap 95% confidence intervals per model configuration. They are filled in after the evaluation pass; see `docs/BUILD_LOG.md`.
