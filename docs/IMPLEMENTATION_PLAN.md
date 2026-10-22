# Orchestrix: local build plan (M0–M8, deployment deferred)

> Approved by the user on 2026-10-21. This is a copy of the plan approved in the planning session. Track status in `docs/BUILD_LOG.md`, not here.

## Context

Build Orchestrix end to end as described in `SPEC.md` (Parts A and B, Sections 0–23). It is a multi-agent orchestration platform: a supervisor plans, specialists execute through MCP tools, a reviewer checks the work, memory persists, humans approve risky steps, and every run is traced and replayable. The build is local only. Everything runs inside WSL Ubuntu 22.04: code, virtualenvs, node_modules, Docker and tooling. Nothing runs on the Windows side. There is no git remote and no push. Helm, kind and release pipelines are deferred until the user asks for deployment.

User decisions:
- Claude Code reopens inside WSL (VS Code Remote-WSL).
- Fully local: no Tavily or Anthropic keys. Search uses SearXNG plus recorded fixtures, and models run on `qwen3:8b`.
- Claude keeps going between milestones and reports after each one.
- The Ollama systemd override is approved.

## Prerequisite check results (2026-10-21)

| Item | Status |
|---|---|
| Ubuntu 22.04.5, WSL2 kernel 6.6, 32 vCPU, 876 GB disk free | OK |
| RAM: 16 GB on the host, WSL capped at 11 GB plus 8 GB swap; Windows had ~0.6 GB free | **Tight.** Budget memory for every Compose service |
| Docker 29.8.2, Compose v5.6.0 (Docker Desktop WSL integration), NVIDIA runtime present | OK |
| uv 0.12.0, Python 3.13.14 (uv) and 3.13.16 (system); system default is 3.10 | OK. Use 3.13 through uv and leave the system Python alone |
| Node v26.8.1 in `~/.local/bin` (becomes LTS about 2026-10-28; at or above the spec floor of 24) | OK. npm still needs checking |
| pnpm | **Missing.** Node 25+ no longer bundles corepack, so install the standalone pnpm into `~/.local` |
| opa, gitleaks, trivy, pre-commit | **Missing.** Run opa, gitleaks and trivy from Docker images through make targets; install pre-commit with `uv tool install` |
| helm, kind | Missing, and not needed until deployment |
| git 2.34.1 (user ankith / ankithm111@gmail.com), make, jq, gcc, kubectl | OK |
| Ollama runs natively in WSL with `qwen3:8b` (tools, thinking) and `mxbai-embed-large` (1024 d, **512-token context**); RTX 4060 with 8 GB | OK. Needs tuning (M0) |
| Ports 5432, 8000, 8001, 8443 and 8444 are held by the user's Kong containers | **Conflict.** Remap Orchestrix ports and leave Kong untouched |
| `wsl.exe` hops from Windows dropped connections repeatedly | Resolved by reopening Claude Code inside WSL |

## Host port map (Kong-safe)

Postgres `55432`, Redis `56379`, S3 `59000/59001`, OPA `58181`, LiteLLM `54000`, API `8100`, web (Vite) `5173`, SearXNG `58888`, Keycloak `8180`, Langfuse `3100`, Grafana `3101`, Prometheus `59090`. MCP servers are reachable only on the internal network; debug ports `59101–59105` are opt-in.

## Improvements over the spec (each with a reason; recorded in docs/BUILD_LOG.md)

1. **Ollama context.** Ollama's default 4096-token context silently truncates planning prompts. Set `OLLAMA_CONTEXT_LENGTH=16384`, plus flash attention and a q8_0 KV cache, so 16k of context fits in 8 GB of VRAM. Also set `num_ctx` per alias in the LiteLLM config.
2. **Embedding input cap.** `mxbai-embed-large` reads only 512 tokens. Build the retrieval query from the title plus a condensed description, and enforce the 500-char memory text limit before embedding.
3. **Tool-call idempotency key.** Use `hash(run_id, checkpoint_ns, tool_call_id)`. The `tool_call_id` comes from the checkpointed AIMessage and stays stable across re-runs. The spec's `hash(args, step)` breaks after a crash, because the LLM regenerates different args and a duplicate side effect slips through.
4. **Missing tables the spec implies.**
   - `email_outbox`: `comms.send_email` "writes to an outbox table", but the table isn't defined.
   - `escalation_messages`: reviewer chat is "stored with the escalation".
   - Add `in_review` to the RunState subtask statuses so they match the `subtasks` table.
5. **Checkpoint serde allow-list.** Recent `langgraph-checkpoint` restricts msgpack deserialisation of unregistered types. Register the Orchestrix Pydantic models with the serializer and add the mandatory RunState round-trip test.
6. **Reranker without PyTorch.** The `local` backend uses `fastembed`'s ONNX cross-encoder (e.g. `BAAI/bge-reranker-base`) instead of sentence-transformers with bge-reranker-v2-m3. That model needs about 2.3 GB of RAM plus torch. The `http` backend (TEI/vLLM) stays as specified.
7. **Shared infra for Langfuse.** Copy Langfuse's upstream compose, but point it at our Postgres 18 (a separate `langfuse` DB), our Redis (noeviction, its own DB index) and our S3 (its own bucket). Only ClickHouse is extra. This saves about 1.5 GB of RAM.
8. **Notional pricing for local models.** Local tokens get a configurable notional price, so budgets, the 80%/100% escalations and the Costs page are exercised offline. The UI labels these costs as notional.
9. **`make ci`** runs exactly what `ci.yml` runs, because nothing is pushed. `ci.yml` and `eval.yml` are still written so they're ready later.
10. **Memory limits** on every Compose service. Dev runs one worker by default; the resilience test scales to 2.
11. **Escalation semantics documented.** Parallel subtasks form one `Send` superstep. An escalated subtask therefore holds the next dependency level until it's answered, which the spec accepts for v1.0. The UI shows "waiting on escalation" for those subtasks.
12. **Idempotency-Key window.** A retention job nulls `tasks.idempotency_key` after 24 h, so the unique constraint only blocks reuse inside the FR-05 window.
13. **Continuity files.** `docs/IMPLEMENTATION_PLAN.md` (this plan) and `docs/BUILD_LOG.md` hold milestone status, resolved versions, decisions and deviations, so work survives context resets and session changes.

## Build sequence

Each milestone follows the same loop. Re-read its SPEC sections, check the installed library source or docs for each API before using it, implement, then write tests named after FR IDs. Then `make ci` must pass, Claude verifies "Done when" itself, updates BUILD_LOG, makes a local commit (with the Co-Authored-By trailer) and posts a short report.

### M0: Bootstrap (start here)
- **In the Windows session, right after approval:**
  - Write `~/maop/SPEC.md` (the full spec, verbatim from the attachment), `CLAUDE.md`, `docs/IMPLEMENTATION_PLAN.md` and `docs/BUILD_LOG.md`.
  - Give the user the Ollama override commands to run with sudo: a systemd drop-in setting `OLLAMA_HOST=0.0.0.0`, `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`, then `daemon-reload` and a restart.
  - Give the user the reopen step (`cd ~/maop && code .`) and a one-line prompt to continue.
- **In the WSL session:**
  - `git init` (local only).
  - Install pnpm with the standalone installer into `~/.local`; `uv tool install pre-commit`.
  - Verify npm.
  - Pull the `openpolicyagent/opa`, `zricethezav/gitleaks` and `aquasec/trivy` images.
  - Verify a container can reach host Ollama: try `host.docker.internal`, then the WSL IP; fall back to a GPU Ollama container that mounts the existing model blobs.
  - Record everything in BUILD_LOG.

### M1: Foundation (spec Sections 7, 9, 10, 21)
- uv workspace: `apps/api`, `apps/worker`, `packages/{core,agents,memory,tools,policy,evals}`, and `mcp_servers/{web,sandbox,data,files,comms}`, each as an `orchestrix_*` package. pnpm workspace with `apps/web` (Vite scaffold). Resolve and pin the latest stable versions and commit the lockfiles.
- `orchestrix_core.config.Settings` (Section 21.1) and structlog JSON logging with `trace_id`/`run_id`/`task_id` context vars.
- Alembic migrations for every Section 10 table, plus the improvement-4 tables, the `updated_at` triggers and the `uuidv7()` defaults.
- Postgres roles `orchestrix_app`, `orchestrix_migrator` and `orchestrix_readonly`; the `demo_data` schema; `REVOKE UPDATE, DELETE` on `audit_log`.
- `infra/compose/docker-compose.yml`, `core` profile:
  - pgvector pg18 with the volume at `/var/lib/postgresql`, and an init script creating the `orchestrix`, `litellm`, `keycloak` and `langfuse` DBs and the roles.
  - Redis 8.2 (noeviction), S3 (whichever store Langfuse upstream currently ships, pinned), OPA, LiteLLM, api, worker and scheduler.
  - A one-shot `migrate` service (alembic plus `checkpointer.setup()`), healthchecks, `depends_on: service_healthy`, and memory limits.
- One shared multi-stage Python Dockerfile (non-root, slim) parameterised by package.
- `/healthz` and `/readyz` (DB, Redis, OPA, LiteLLM).
- `infra/litellm/config.yaml` with the local profile, `num_ctx` and thinking disabled for non-planner aliases. `make gateway-check` sends one chat request and one JSON-schema structured-output request per alias, and an embed request.
- Makefile targets: `dev`, `dev-full`, `migrate`, `seed`, `test`, `test-integration`, `lint`, `typecheck`, `gateway-check`, `ci`, `opa-test`, `gitleaks`.
- ruff, pyright strict on `apps/api` and `packages/*`, the pre-commit config, and the `ci.yml` skeleton.
- **Done when:** `make dev` brings every core service up healthy; `make migrate` creates every table; `make gateway-check` passes for every alias; `make ci` passes.

### M2: Graph core on fake models (Sections 4.1–4.4, 8, 11, 17, 20.1)
- **De-risk spikes first,** each kept as a permanent test:
  - A `create_agent` called inside a node within a `Send` worker raises `interrupt()` and resumes without repeating earlier tool calls.
  - Two parallel interrupts are resumed by ID in either order.
  - RunState round-trips through `AsyncPostgresSaver`.
- `orchestrix_core.schemas` (Section 11.1 plus events), the `state_machine.py` FR-01 transitions, and `budgets.py`.
- `orchestrix_agents`: `state.py` with reducers (including `add_usage`) and all 13 nodes under `nodes/` (Section 11.3).
- `graph.py` with `route_after_validate`, `route_dispatch` (Send fan-out, concurrency slots, skip cascade) and `route_final`.
- The `subtask_worker` subgraph (11.5), using stub specialists.
- `validate_plan`: schema checks, Kahn's algorithm for cycles, and checks for specialists, allow-lists and input references.
- `ScriptedChatModel` test double in `tests/fixtures`.
- Worker:
  - Taskiq `RedisStreamBroker`.
  - `start_run` and `resume_run` jobs.
  - `lease.py`: acquire, 15 s heartbeat, release, reaper every 30 s, crash-loop guard after 3 reclaims.
  - The `run_events` outbox with Redis publish, plus a relay for unpublished rows.
  - The cancel flag.
- API: `POST /v1/tasks` (202), `GET /v1/tasks/{id}`, `POST /v1/tasks/{id}/cancel`, and `GET /v1/runs/{id}/events` (SSE that replays from Postgres, then relays Redis, dedupes by id, pings every 15 s). Auth mode `dev` with locally signed JWTs; the claims are the same shape Keycloak issues later.
- **Done when:**
  - Graph tests cover every route in 11.3–11.5 in under 60 s.
  - An API-submitted task runs end to end on fakes and streams events.
  - The resilience test passes: SIGKILL a worker mid-run, and another worker completes the run with no duplicated steps or tool calls (NFR-01).

### M3: MCP tools and real specialists (Sections 12, 15.2, 15.3, 16)
- Five FastMCP servers, each with Streamable HTTP at `/mcp` and a `/healthz`, Pydantic-validated inputs, `{error_code, message, retryable}` errors, and service-token auth. They read the `X-Run-Id` and `traceparent` headers.
- Individual tools:
  - `web.search`: Tavily if a key is set, otherwise SearXNG. Add a `searxng` service to the core profile.
  - `web.fetch`: trafilatura extraction plus the SSRF guard (DNS-resolved IP checks, re-checked on each of up to 3 redirect hops).
  - `data.sql_query`: sqlglot SELECT-only parsing, forced `LIMIT 1000`, 10 s timeout, read-only role.
  - `files.*`: backed by S3.
  - `comms.send_email`: writes to `email_outbox`.
- Sandbox: a separate image with pandas, numpy, scipy and matplotlib; non-root, read-only root filesystem, tmpfs per call, rlimits, `internal: true` network, `cap_drop ALL`, `no-new-privileges`, and limits on pids, CPU and memory.
- Tools: the registry sync into the `tools` table and `MultiServerMCPClient`.
- Specialists: `create_agent` per specialist, with Jinja2 prompts in `prompts/` and middleware for budget, policy (stubbed to allow), injection guard with taint tracking, tool limit and tracing. Use built-ins only where they match the spec exactly.
- `llm.py`: an alias factory, `json_schema` structured output with a function-calling fallback, and stripping of `<think>` blocks.
- `make record-fixtures`: records model and web responses into `tests/fixtures` with secrets scrubbed.
- **Done when:**
  - The showcase request (5.6), minus the email approval, completes on the local profile.
  - Sandbox tests prove there is no network, the root filesystem is read-only and the time limit is enforced.
  - Injection test pages are flagged.

### M4: Human-in-the-loop and policy (Sections 4.5, 14, 15.1)
- `policies/` Rego v1 (`orchestrix.tool`, `orchestrix.escalation`) with `opa test` (Docker). The tool registry is pushed to OPA as data. `PolicyClient` uses a 200 ms timeout and fails closed.
- `PolicyMiddleware` calls `interrupt()` before sensitive tools. Implement every trigger and level in 14.1, configured by `escalation_policy.yaml`.
- Implement the `EscalationPayload` and `EscalationDecision` schemas, and the pause/resume sequence from 14.3:
  - Upsert escalation rows when the run pauses.
  - Release the lease.
  - On resume, build `Command(resume={interrupt_id: …})`.
  - Reconcile pending interrupts from `aget_state(...).interrupts` after every stream.
- API: escalations inbox, detail, decision (OPA permissions, four-eyes rule, 409 on an already-resolved escalation) and reviewer chat (FR-44).
- Slack webhook notifier (no-op without a URL), SLA jobs (re-notify at 24 h, fail at 72 h), and audit-log writes.
- **Done when:**
  - Every 14.1 trigger has a test.
  - Two parallel escalations resume correctly in either order.
  - The resume-twice idempotency tests pass.
  - A requester cannot approve their own escalation.
  - With OPA down, decisions are deny.

### M5: Observability and cost (Sections 4.7, 15.3, 19)
- `observability` profile: Langfuse v4 web and worker plus ClickHouse, copied from upstream with versions pinned and pointed at the shared infra (improvement 7). Add Prometheus and Grafana.
- Tracing:
  - Langfuse SDK with the LangChain `CallbackHandler` in the graph config.
  - Custom OpenTelemetry spans for policy, memory, MCP calls (with `traceparent` propagation), escalations and budget decisions.
  - GenAI attributes and a PII-redaction span processor.
- Accounting: `llm_calls`/`tool_calls` rows, LiteLLM response cost or the notional price table, `BudgetMiddleware` enforcement end to end, and the 80% notify / 100% pause escalations.
- Section 19.2 metrics; three Grafana dashboards in `infra/grafana/`; `GET /v1/runs/{id}/trace` and `GET /v1/costs`.
- **Done when:**
  - A showcase run shows a complete nested trace in Langfuse.
  - App cost matches the gateway cost within ±5%.
  - A budget-capped run pauses at 100%.
  - The dashboards render live data.

### M6: Long-term memory (Sections 4.6, 13)
- `orchestrix_memory`:
  - **Write path:** candidates from the `fast` alias; FR-56 validation (facts must cite a run source; preferences must quote the user exactly); PII redaction; dedupe at cosine ≥ 0.92; namespace routing.
  - **Retrieval:** the hybrid RRF SQL from 13.3 with bound parameters, then `RerankClient` (fastembed local or http), then decay scoring, keeping the top 8. Track access and update `last_used_at` for `used_memory_ids`.
  - **Maintenance:** nightly consolidation in the scheduler; deletion that cascades to derived summaries; full-user deletion.
  - **Embedding guard:** an `app_meta` check on worker start.
- API: `GET`/`PATCH`/`DELETE /v1/memories` with audit rows.
- **Done when:**
  - The seeded lesson is retrieved in the top 8.
  - Deleting a memory removes the summaries derived from it.
  - Invalid facts and preferences are rejected. Tested against a testcontainers pgvector database.

### M7: Frontend and authentication (Sections 2, 5, 17, 18)
- `auth` profile: Keycloak 26 with a realm export containing four roles, demo users across two teams and a PKCE client. API: PyJWT `PyJWKClient` validation, a `require_role` dependency and repository-level ownership checks. `AUTH_MODE=dev` refuses to start unless `ENV=development`.
- `apps/web` stack: React 19, Vite, TS strict, TanStack Router and Query, Tailwind v4 with shadcn/ui, lucide icons and Biome.
- Data layer: `pnpm gen:api` generates the client with openapi-typescript and openapi-fetch. `useRunEvents` uses fetch-event-source and patches the query cache.
- Screens: all ten in 18.1. The plan DAG and trace use React Flow with elkjs. The decision view uses schema-driven edit forms. Markdown is sanitised. Auth is oidc-client-ts with tokens kept in memory.
- Accessibility (WCAG 2.2 AA): status always shown as colour plus icon plus label; light and dark themes.
- Tests: Vitest, plus Playwright for journeys 5.1–5.5 against Compose with recorded fixtures.
- **Done when:**
  - All five journeys pass in Playwright.
  - The decision view works with the keyboard alone.
  - The API layer has no `any` types.

### M8: Replay, evals, hardening and demo (Sections 19.4, 20, 23), local parts only
- Replay:
  - Checkpoint history endpoint.
  - Replay with `aupdate_state` forking, `head_checkpoint_id` pinning and simulated side effects.
  - The diff endpoint and the UI.
- `orchestrix_evals`:
  - All six datasets.
  - Deterministic checks plus an LLM judge.
  - 3 runs per case with bootstrap 95% confidence intervals.
  - A kappa calibration CLI; the user supplies the two human labelers.
  - A Markdown report.
  - `eval.yml` written but not run.
- Local constraint: on `qwen3:8b`, the full suite (40 golden tasks × 3 runs) takes many hours. `make eval-smoke` runs 10 tasks. `make eval-full` runs overnight in the background. The report states the local-profile numbers honestly.
- Hardening:
  - Trivy and gitleaks scans through Docker (`make scan`).
  - Retention jobs.
  - The rate limits and security headers from 15.4.
- `make demo`: seeds users, memories, the CSV and the web-search fixture, then runs 5.6 offline.
- README as onboarding docs, with the architecture diagram and how to add a specialist or tool.
- Recording the 5-minute demo video is the user's job.
- **Done when:**
  - The Section 1 metrics are measured and reported, whether met or not.
  - `make demo` reproduces the showcase offline.
  - Every 23.3 checklist item passes except the Helm and kind deployment.

### Deferred until the user asks for deployment
Helm chart, kind, NetworkPolicies, the gVisor RuntimeClass, Argo CD, `release.yml`, GHCR, and any `git push`.

## Risks to watch

| Risk | Mitigation |
|---|---|
| RAM exhaustion with the full stack, observability and Keycloak | Memory limits; profiles turned on only when needed; shared Langfuse infra; one worker in dev |
| An 8B model producing weak plans and reviews, or failing structured output | JSON-schema mode with a function-calling fallback and one repair retry; thinking off; deterministic validation; small plans |
| Drift in LangGraph, LangChain, LiteLLM or Langfuse APIs | Read the installed source before using an API; pinned lockfiles; spikes in M2 |
| Containers unable to reach host Ollama | Verified in M0, with the GPU-container fallback |
| Reviewer and specialist sharing a model locally (weakens FR-32) | Different prompt and temperature 0; `reviewer_model` recorded and reported in evals |

## Verification (end to end)
- After every milestone: `make ci` (ruff, Biome, pyright, tsc, unit and graph tests, `opa test`, integration tests with testcontainers, Vitest, OpenAPI drift check, Docker builds, gitleaks), then the milestone's "Done when" checks, run by Claude.
- Final checks:
  - `make dev-full`, then submit the showcase task from the UI and watch it plan, run in parallel, get rejected and revised, pause for email approval by a second user, then deliver and write memory.
  - Walk through the Trace Explorer and run a replay from a checkpoint.
  - Run the SIGKILL resilience test and a full-stack restart mid-pause.
  - Pass `make eval-smoke` and Playwright 5.1–5.5.
  - Run `make demo` offline.
