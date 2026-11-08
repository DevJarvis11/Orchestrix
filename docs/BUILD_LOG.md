# Build log

The running record of milestone status, resolved versions, decisions and deviations. Newest entries go at the bottom of each section.

## Milestone status

| Milestone | Status | Commit | Notes |
|---|---|---|---|
| M0 Bootstrap | Done | c7bc0aa | 2026-10-22: tool images pulled. Ollama is reached at the WSL IP (the Makefile derives it). The VPN subnet clash is fixed (the Kong network moved to 172.30.0.0/16). |
| M1 Foundation | Done | a906f0a | 2026-10-25: pushed to GitHub (rebased onto the repo's initial MIT LICENSE commit). |
| M2 Graph core | Done | (this commit) | 2026-10-25: 25 graph route tests (0.8 s), the create_agent interrupt spike, 24 integration tests, the fake-model API stream e2e, and the SIGKILL resilience test (NFR-01) all pass. |
| M3 MCP and specialists | Done | (this commit) | 2026-11-08: showcase 5.6 completed live on gpt-oss-120b (VPN only, 646 s, 4/4 subtasks approved, memory written; email step not reached); sandbox isolation, credential isolation and time limit proven live; injection pages flagged. |
| M4 HITL and policy | Done (live email journey pending) | (this commit) | 17 Rego tests; four-eyes, 409, fail-closed and decision tests on a real OPA; live clarify, review and policy pauses resumed by a second user. |
| M5 Observability and cost | Code complete | (this commit) | Gateway cost header recorded per call; efficiency, costs and trace endpoints tested. Not yet run: observability profile live, the ±5% cost match, a budget-capped live run. |
| M6 Long-term memory | Done (eval pending) | (this commit) | Seeded lesson embedded (2560-d); 3 memories written by the live run; deletion cascade and FR-56 validation tested. |
| M7 Frontend and auth | Code complete | (this commit) | Ten screens; lint, typecheck, Vitest and build pass. Not yet run: Playwright journeys 5.1-5.5, Keycloak login in a browser. |
| M8 Replay, evals, demo | Code complete | (this commit) | Replay API tested; 5 eval datasets + harness; resilience test passes; scan and gitleaks clean. Not yet run: eval smoke, `make demo` offline. |

## Next action

2026-11-08: M3-M8 committed together (they were written and integration-tested as one change set). `make ci`, `make test-stack` and `make test-resilience` pass; the showcase completed live on gpt-oss-120b.

Remaining live verification: Playwright journeys 5.1-5.5 (to write), the observability profile with the ±5% cost check, the email-approval journey live, Keycloak login in a browser, `make eval-smoke` (needs the VPN, about 1-2 h of model calls: ask the user first), `make demo` offline (needs recorded fetch fixtures: `make record-fixtures`).

Open items carried over: the 1-year audit-log retention needs the migrator role.

## Environment facts (from the 2026-10-21 prerequisite check)

- Ubuntu 22.04.5 on WSL2 (kernel 6.6.114.1), 32 vCPU, WSL memory 11 GB plus 8 GB swap (`.wslconfig`), host RAM 16 GB.
- Docker 29.8.2, Compose v5.6.0, through Docker Desktop with WSL integration. The NVIDIA runtime is available.
- uv 0.12.0, with CPython 3.13.14 installed by uv. The system python3 is 3.10.12 (don't use it).
- Node v26.8.1 at `~/.local/bin/node`. pnpm is not installed.
- Ollama is host-native (`/usr/local/bin/ollama`) with `qwen3:8b` (Q4_K_M, 40,960 context, tools and thinking) and `mxbai-embed-large` (1024 d, 512 context). The GPU is an RTX 4060 Laptop with 8 GB.
- The user's containers `kong-gateway` and `kong-database` hold ports 5432, 8000, 8001, 8443 and 8444. Don't touch them.
- git 2.34.1, user `ankith` <ankithm111@gmail.com>.

## Resolved versions

Resolved 2026-10-22. `uv.lock` and `pnpm-lock.yaml` are authoritative.

| Tool or library | Version | Notes |
|---|---|---|
| uv | 0.12.0 | The Docker builds pin `ghcr.io/astral-sh/uv:0.12.0` to match the lockfile writer (0.13.5 exists) |
| pnpm | 12.15.0 | Installed with `npm i -g` into `~/.local` (no corepack) |
| pre-commit | 4.7.0 | `uv tool` |
| npm / Node | 11.19.0 / 26.8.1 | |
| Python base image | `python:3.13.17-slim-trixie` | |
| fastapi / uvicorn | 0.159.1 / 0.54.0 | |
| pydantic / pydantic-settings | 2.14.1 / 2.16.1 | |
| sqlalchemy / alembic | 2.1.6 / 1.20.1 | SQLAlchemy 2.1 is above the spec's "2.0.x latest" floor |
| psycopg / pgvector (py) | 3.3.6 / 0.5.1 | |
| langgraph-checkpoint-postgres | 3.2.0 | |
| redis (py) / taskiq / taskiq-redis | 8.1.0 / 0.13.0 / 1.2.4 | |
| structlog / httpx / tenacity | 26.1.0 / 0.28.1 / 9.2.1 | |
| ruff / pyright / pytest / pytest-asyncio | 0.17.1 / 1.1.414 / 9.1.1 / 1.4.0 | |
| testcontainers / respx | 4.15.0 / 0.23.1 | |
| langgraph / langchain / langchain-openai | 1.2.17 / 1.4.7 / 1.7.2 | M2 |
| jinja2 / pyjwt / aioboto3 / asgi-lifespan | 3.1.6 / 2.15.1 / 15.5.0 / 2.1.0 | M2 (aioboto3 pins wrapt 1.17.3) |
| Postgres image | `pgvector/pgvector:0.8.7-pg18-trixie` | |
| Yotta models (consumed, not managed) | `gpt-oss-120b`, `qwen3-embed` (2560-d), `qwen3-rerank` | Reached through our proxy with the `litellm_proxy/` provider at `http://172.18.220.50:4000` (no `/v1`) |
| Redis image | `redis:8.10.2` | |
| S3 image | `cgr.dev/chainguard/minio@sha256:d88920d6…2880` | What Langfuse upstream ships; pinned by digest |
| OPA image | `openpolicyagent/opa:1.21.3` | Distroless; the healthcheck uses `opa eval` |
| LiteLLM image | `ghcr.io/berriai/litellm-database:v1.106.2` | Config checked against the 1.106.2 source |
| gitleaks / trivy images | v8.30.1 / 0.76.1 | |
| react / vite / typescript / biome | 19.3.1 / 8.3.x / 6.0.x / 2.6.2 | |

## Decisions and deviations

- 2026-10-21: The user approved the plan's 13 improvements over the spec (see the plan). These take precedence over SPEC.md where they conflict.
- 2026-10-21: Fully local profile, with no Tavily or Anthropic keys. Web search uses SearXNG plus fixtures.
- 2026-10-21: Claude continues between milestones without waiting and reports after each one.
- 2026-10-22: **Amendments A1–A5** (see the plan): VPN `gpt-oss-120b` is primary with a `qwen3:8b` fallback; roles are resolved app-side with a per-task override and an admin default; the run records which models served it; embeddings stay on local mxbai (1024-d); rerank uses VPN `qwen3-rerank` with a local fallback. The user asked Claude to pick the embedding model; Claude chose local mxbai.
- 2026-10-22: The Yotta key lives only in `.env` (gitignored, mode 600). The `.gitleaks.toml` rule `yotta-litellm-key` catches `sk-yotta-…`. The SSH details in the Yotta README are not used.
- 2026-10-22: The LiteLLM proxy chains to the Yotta gateway through the `litellm_proxy/` provider. It passes `reasoning_effort`, `response_format` and `tools` through unchanged.
- 2026-10-22: qwen3 deployment settings:
  - `think: false` and `num_ctx: 16384` are deployment params; `ollama_chat` puts `num_ctx` in Ollama `options`.
  - `additional_drop_params: [reasoning_effort]` stops a gpt-oss effort carried over by a fallback from turning thinking back on.
  - Verified locally with the 1.106.2 proxy: chat, json_schema, tool calls, a 1024-d embedding, and a real fallback on a remote failure (`x-litellm-attempted-fallbacks: 1`).
- 2026-10-22: LiteLLM refuses `mock_testing_fallbacks` unless a `dangerously_…` flag is set. Instead, gateway-check uses a `fallback-probe` deployment on a refused port (`127.0.0.1:9`). The app must hide it from the model picker.
- 2026-10-22: The ruff 0.17 formatter rewrites Python code blocks in Markdown, and it reformatted SPEC.md (since restored). `*.md` is now excluded from ruff.
- 2026-10-22: Migration 0001 was rendered from the models with Alembic's renderer, because no DB was available. `alembic check` (drift) runs in the integration tests. LangGraph's checkpoint tables are excluded through `include_object`. Migrations and `checkpointer.setup()` run as `orchestrix_migrator`, and default privileges give `orchestrix_app` DML on everything, including the checkpoint tables.
- 2026-10-22: Improvement-4 tables as built:
  - `email_outbox`: `run_id`, `tool_call_id` UNIQUE, `to_addrs text[]` (1–10), `subject`, `body_md`, `status` default `'simulated'`.
  - `escalation_messages`: `escalation_id`, `author_id` (null means the agent), `role` (`reviewer` or `agent`), `body`.
  - `updated_at` triggers on `tasks`, `tools` and `model_defaults`.
- 2026-10-22: The initdb script also creates `litellm`, `keycloak` and `langfuse` roles, each owning its own DB. `REVOKE ALL ON DATABASE orchestrix FROM PUBLIC`.
- 2026-10-22: Frontend deviations: Biome replaces the template's oxlint (as the spec requires). TypeScript stays on the Vite template's ~6.0 instead of the new native TS 7.0.2; this still meets the spec's TS 5.x floor.
- 2026-10-22: `ORCHESTRIX_MODEL_PROFILE` is kept for SPEC 21.1 compatibility, but there is now a single catalogue config. Profiles are effectively replaced by A2 runtime switching.

- 2026-10-22: **Ollama URL.** From containers, `host.docker.internal` resolves to the Windows host, which runs a *different* Ollama (0.40.4, no models). The WSL Ollama (0.30.6, with the models) is reachable at the WSL IP. The Makefile exports `OLLAMA_BASE_URL=http://$(hostname -I):11434` on every run, because the IP can change across WSL restarts.
- 2026-10-22: **VPN subnet clash.** The user's `kong-ai-gateway_default` Docker network is 172.18.0.0/16. Inside the Docker Desktop VM, the VPN host 172.18.220.50 routes into that bridge, so containers cannot reach the Yotta gateway. WSL itself can. Not fixed, because those are the user's Kong resources. Until it's fixed, the remote probes time out and LiteLLM falls back to qwen3.
- 2026-10-22: **API host port.** 8100 is held by the user's AGR_Agent uvicorn (outside Docker). Compose now takes `API_HOST_PORT` (default 8100). Verification used 58100.
- 2026-10-22: **OPA healthcheck.** The `-debug` image (Wolfi busybox) has no wget or nc. The stack uses the distroless `openpolicyagent/opa:1.21.3`, whose healthcheck is `opa eval --fail` on `{200: true}[http.send(/health).status_code]`. A plain comparison would not work: `opa eval` reports `false` as a defined value.
- 2026-10-22: **Image pulls.** Docker Desktop's credential helper fails intermittently on parallel pulls ("error getting credentials"). Pull sequentially (retry) before `make dev` on a fresh machine.
- 2026-10-22: **gateway-check.** A direct probe fails if LiteLLM reports a fallback (`x-litellm-attempted-fallbacks` > 0), so a dead remote model can't pass silently. The remote cooldown was raised to 300 s.
- 2026-10-22: **Measured memory.** The core stack uses about 1.05 GB (LiteLLM 613 MB, S3 108 MB, Postgres 89 MB, others under 80 MB each). WSL had about 3 GB available, with the user's other IDE servers and agents running.

- 2026-10-22: **The VPN subnet clash is fixed** (approved by the user).
  - The user's Kong compose file (`OneDrive…/Desktop/kong-ai-gateway/docker-compose.yml`) now pins `172.30.0.0/16`.
  - Backups were taken first: `~/kong-backup-2026-10-22.sql` (pg_dumpall) and `~/kong-docker-compose.yml.bak-2026-10-22`.
  - The recreate kept the same anonymous data volume, and Kong still has 1 service and 1 route.
  - The Orchestrix network is pinned to `172.29.0.0/16`, so no future auto-assigned network grabs 172.18.0.0/16 (the VPN route is 172.18.220.0/23).
- 2026-10-22: **API host port is now 58100** (approved by the user; 8100 is the user's AGR_Agent). Updated in CLAUDE.md, AGENTS.md, the plan and Compose (`API_HOST_PORT` overrides).
- 2026-10-22: **Embeddings and rerank moved to Yotta** (revised A5, at the user's request): `qwen3-embed` 2560-d `halfvec`, nullable with backfill, and `qwen3-rerank`. Migration 0001 was edited in place (never committed), and the dev DB was re-migrated (downgrade base, then upgrade).
- 2026-10-22: **`REMOTE_LLM_BASE_URL` has no `/v1`.** LiteLLM's `litellm_proxy` rerank 404s with a `/v1` base. The Yotta gateway serves chat, embeddings and rerank on the root routes.
- 2026-10-22: **Cooldowns.** A router-wide `allowed_fails` switches LiteLLM to legacy cooldowns and benched the single local fallback. Now only `gpt-oss-120b` has `model_info.allowed_fails_policy` (Timeout, InternalServer, ServiceUnavailable and BadGateway each at 0) and `cooldown_time: 300`. `num_retries` is 1.
- 2026-10-22: **Local model cold loads.** WSL swap was about 7.3 of 8 GB used (the user's other IDEs and agents), and loading qwen3 took about 250 s. The local Ollama deployment now sends `keep_alive: 30m`, its timeout is 300 s, and gateway-check waits 360 s (longer than the proxy). Once warm, local calls take under 1 s.
- 2026-10-22: **User rule: Yotta is consume-only.** Never restart, SSH into or reconfigure it.

- 2026-10-25: **GitHub.** Commits are pushed to the private repo `https://github.com/DevJarvis11/Orchestrix.git` (the user's decision; this replaces "local only, no push").
  - Auth is a fine-grained PAT kept in a repo-local credential store, `~/.config/git/orchestrix-credentials` (mode 600). The global git-credential-manager holds a GPG-locked token for another account (`Ankith-M-QG`), which non-interactive pushes can't use.
  - `ci.yml` runs on every push and calls `make env` before `make ci`. `eval.yml` (M8) is manual-dispatch only, because GitHub runners can't reach Yotta.
- 2026-10-25: **Cadence.** After each milestone passes `make ci` and its Done-when checks, Claude commits, pushes, reports and continues (the user's choice). The user reviews commit by commit.

- 2026-10-25: **M2 graph decisions.**
  - **RunState gains `budget` and `stop_reason`.** The effective caps must survive a resume after a human raises them at `budget_100`; `RunContext` is immutable and not checkpointed. `stop_reason` routes cancel, budget and reject straight to `finalize`.
  - **Graph wiring extends SPEC 11.4 minimally:**
    - `plan_gate` routes back to itself while an edited plan is still invalid, and to `finalize` when rejected.
    - `dispatch` routes to `finalize` on a stop.
    - `final_review` routes to `finalize` when a human rejects.
  - **Review thresholds.** A reviewer "approve" below `review_score_threshold` (6) counts as a revision request (FR-40). After a final-review escalation, a human "approve" or "take_over" delivers without another automated review.
  - **FR-11 allow-list check.** The `Plan` schema has no tool field, so the check applies to `attachment:` input references, which need `files.read_artifact`. `sN.output` inputs must also appear in `depends_on`.
  - **Budget escalation at `dispatch`.** `budget_100` raises an `approve_action` interrupt: `approve_with_edits` raises the caps, `reject` stops with `budget_exceeded`. The full 14.1 trigger set (BudgetMiddleware, `notify`) is M4.
  - **§14.2 schemas.** `EscalationPayload` and `EscalationDecision` are implemented exactly as specified and are already used by every M2 interrupt. The worker applies posted decisions (`escalation_actions`) on resume. The decision API itself is M4.
  - **Specialist stub.** M2 specialists are a one-call stub (`StubSpecialistRunner`). LLM-call and cost rows (`llm_calls`) come in M5.
- 2026-10-25: **Migration 0002** adds `runs.reclaim_count` for the crash-loop guard (SPEC 8.2 step 5 needs a counter that the schema lacked).
- 2026-10-25: **Fake-model mode.** `ORCHESTRIX_FAKE_MODELS` (refused in production) runs deterministic models in the worker. Its specialist writes one idempotent `tool_calls` row per subtask, keyed by `hash(run_id, checkpoint_ns, …)` (improvement 3). `make test-resilience` uses it with 2 workers and 10 s leases to prove NFR-01.
- 2026-10-25: **Queue.** The Taskiq broker lives in `orchestrix_core.queue`, so the API enqueues `start_run` by task name without importing worker code. The worker command imports `orchestrix_worker.jobs` and `orchestrix_worker.scheduler`. The scheduler runs the reaper every 30 s, the outbox relay every 10 s, and the idempotency-key expiry hourly.
- 2026-10-25: **SSE.** Uses FastAPI 0.159's built-in `fastapi.sse`, whose idle keepalive sends `: ping` every 15 s (exactly SPEC 17.2). The stream closes after `run.completed`, `run.failed` or `run.cancelled`.
- 2026-10-25: **Dev auth.**
  - Tokens are HS256 JWTs shaped like Keycloak's (`realm_access.roles`), signed with `ORCHESTRIX_DEV_JWT_SECRET` (at least 32 chars, generated into `.env`).
  - Users are provisioned just-in-time; the team comes from `team_members`.
  - Task read access is owner or team member; full RBAC is M7.
  - `make dev-token` provisions a demo user and team.
- 2026-10-25: **Tooling.**
  - `langgraph` is a namespace package, so pyright ignores its `py.typed` for `langgraph.graph`. `graph.py` and `subgraph.py` carry file-level relaxations for three reportUnknown/MissingTypeStubs rules.
  - The root dev group depends on every workspace member, so `uv run` never un-installs them.
- 2026-10-25: **Admin model defaults.** The `model_defaults` table is not read yet: role resolution uses task override, then settings default. The admin defaults API arrives in M4 (A4).

- 2026-11-01: **Amendment A6** (per-run efficiency breakdown) approved by the user. It is built in M5 (extra `llm_calls` columns plus an efficiency endpoint) and shown in M7. Automatic token optimisation is out of scope until the evals show where the waste is.
- 2026-11-01: **`mcp` is pinned to 1.30.0**, not 2.3.0: `langchain-mcp-adapters` 0.3.2 requires `mcp<2`, and the workspace has one lockfile.

- 2026-11-07: **Build all, then test (user's decision).** The remaining milestones (M3–M8) are written back to back; tests for each module, `make ci` and the Done-when checks run in one integration pass at the end. Nothing is committed or pushed until that pass is green. ruff and pyright are kept clean as code lands. This overrides, for this stretch, CLAUDE.md's "tests in the same commit" and "finish, test, commit one milestone before the next".
- 2026-11-07: **The VPN is on request.** The user turns the Yotta VPN on when asked; VPN-dependent checks are batched.
- 2026-11-07: **Sandbox has no storage access (security fix).** Untrusted code could read the sandbox server's environment through `/proc/1/environ` (same uid), which held the S3 root keys, and S3 was on the sandbox network. Now:
  - `run_python` takes `input_files` (bytes) and returns `created_files`. The worker's `SandboxFiles` MCP interceptor resolves the model's `artifact_ids` with a run-scope check (`SqlArtifactBridge`), stores created files under deterministic ids, and registers them. `input_files` is hidden from the model's schema.
  - mcp-sandbox has no S3 env and S3 left the `sandbox` network. The server also sets `PR_SET_DUMPABLE=0`.
- 2026-11-07: **Specialist middleware order:** Budget (before_model), then ToolCallLimit, InjectionGuard, ToolAccounting, Policy, ToolRetry, tool. Accounting sits outside Policy so a call that already ran is answered from its row and never asks a human twice. Policy is the real OPA check (not a stub). FR-24 retry is our own middleware: MCP errors arrive as error ToolMessages, which the built-in ToolRetryMiddleware (exceptions only) never sees. Specialist time limits (SPEC 12.1) use `asyncio.timeout`; a human pause stops the clock.
- 2026-11-07: **Budget estimate** before a model call is chars/3.5 plus per-message overhead, not the gateway's token counter (one fewer round-trip per call; it over-estimates). A human-raised budget is stored in the agent's state (`orchestrix_budget`) and merged into RunState with an elementwise-max reducer. Active time for the FR-63 time cap is `runs.active_seconds` (migration 0004), accumulated per lease.
- 2026-11-07: **Graph changes (idempotent resume, SPEC 11.6):**
  - `final_review` (model call) and `final_gate` (human decisions: low_review_score, user_requested_review) are separate nodes. Before, a resume re-ran the reviewer model ahead of the interrupt and could drop the human's answer.
  - FR-15 clarify: the plan node asks a `Clarity` question once; a new `clarify` node pauses (level clarify, action answer). Answers are kept as `clarifications`, the requester's own words for FR-56. `ORCHESTRIX_CLARIFY_ENABLED` turns it off.
  - FR-40 "explicit user request": `tasks.require_final_approval` (migration 0004).
  - Plan-cited memories are marked used in `write_memory`, once per run.
- 2026-11-07: **Accounting (A6):** `llm_calls` gains requested_model, purpose, subtask_key, attempt and revision (migration 0004). An `LlmCallRecorder` callback is attached to every gateway chat model, with a `CallTag`. Cost comes from LiteLLM's `x-litellm-response-cost` header, else the price table.
- 2026-11-07: **Policy:** `policies/tool.rego` and `escalation.rego` as in SPEC 15.1, plus "unknown tool" denied. OPA keeps the registry in memory, so the gate re-pushes it once if a catalogued tool comes back unknown (OPA restarted). Denials are audited without argument values. Trigger-to-level mapping and SLA timings live in `orchestrix_policy/escalation_policy.yaml`.
- 2026-11-07: **Observability:** Langfuse SDK 5.2 (OTel-native) with our tracer provider and a PII `mask`. The trace id is derived from the run id, so resumes join one trace. Tracing is opt-in in Compose (`make obs`, `make dev-full`). The observability profile copies Langfuse's upstream compose (fetched 2026-11-07, v4) pinned to langfuse 4.76.0, clickhouse 26.3.62.3, prometheus v3.16.0 and grafana 13.3.0, pointed at our Postgres (`langfuse` DB), Redis DB 1 and the `langfuse` bucket. The AI-gateway service is omitted. `make env` now appends missing keys to an existing `.env`.
- 2026-11-07: **Memory retrieval deviation:** the keyword arm ORs the task's lexemes (`websearch_to_tsquery` ANDs every word of a long task and rarely matches). Local rerank uses fastembed `BAAI/bge-reranker-base`, with logits put through a sigmoid.

- 2026-11-08: **Model-agnostic by configuration (user's requirement).** No code names a model or a model family:
  - `ORCHESTRIX_DEFAULT_MODELS` (role -> catalogue model) in `.env` is the one switch; admin defaults and per-task overrides still win (A2).
  - Prices moved into `infra/litellm/config.yaml` (`input_cost_per_token`); the gateway's `x-litellm-response-cost` header is the cost source. `settings.model_prices` is only an override.
  - Per-role `reasoning_effort` is sent to every model; LiteLLM `drop_params: true` drops it where unsupported.
  - `gateway-check` discovers models from the proxy's `/model/info`; the showcase, demo and eval targets default to the configured models.
  - `env_ignore_empty=True` in Settings, so a Compose variable left empty means "use the default".
- 2026-11-08: **VPN models only (user's instruction).** `ORCHESTRIX_LOCAL_FALLBACK=false` sends `disable_fallbacks` to LiteLLM, drops the local reranker fallback and skips local probes. Over the VPN only model inference calls are made.
- 2026-11-08: **Live-run fixes (gpt-oss-120b showcase):**
  - The gateway benched the only VPN model for 5 minutes after one 120 s timeout, failing every later call with 429. Timeouts no longer trigger cooldown; the deployment timeout is 180 s, and the client waits `GATEWAY_TIMEOUT` = 420 s (longer than 180 s x 2 attempts).
  - A specialist that ends in prose instead of the structured-output tool gets one more structured call (`invoke_structured`) over a compact transcript.
  - The branch head is saved as each root checkpoint is written, so a reclaimed run resumes from its latest step.
  - After a human decision, clarify, escalate and final_gate set the task back to their phase (planning, running, reviewing). Before, `awaiting_human -> completed` raised at the very end. The graph-test recorder now enforces FR-01 and the harness applies the worker's pause status, so this class of bug fails in tests.
- 2026-11-08: **Code review fixes:** files.read_artifact refuses unknown runs (was `task_id IS NULL`); sandbox drains pipes for at most 5 s after a kill; audit rows record the human-edited arguments that ran; attachment names must be unique per task; team sync reads first and inserts with ON CONFLICT; the API rate limit runs before any database write; escalation visibility follows the assigned team; `web.fetch` connects to the checked IP with the real Host header and SNI (DNS-rebinding window closed); the trace endpoint lists every model call exactly once.
- 2026-11-08: **web.fetch gzip fix (found live):** the capped reader decoded gzip, then built a Response that still claimed `Content-Encoding: gzip`, so 7 of 8 live fetches failed. The decoded copy now drops encoding and length headers (test added).
- 2026-11-08: **Deliverables:** project artifact (claude.ai, private) built from `docs/artifact/index.html` with the four diagrams; Word documentation `docs/Orchestrix-Project-Documentation.docx` (A4, validated); `docs/RUN_LOCALLY.md`.
- 2026-11-08: **Structure:** one S3 store (`orchestrix_core.storage.S3ObjectStore`, `artifact_key`) replaces the duplicate in `orchestrix_tools.objects`; `GatewayEmbedder.from_settings`; dead `lease.py` and the unused `ollama_base_url` setting removed; shared API test fixtures and a real OPA container live in `tests/integration/conftest.py`; `record_fixtures.py` drives mcp-web's own record mode (searches and fetched pages).
- 2026-11-08: **Diagrams:** four Archify diagrams (architecture, run graph, approval sequence, task lifecycle) in `docs/diagrams/`, each passing validate, deliver, check and the real-browser gate. They are authored from the code without pinned source links (the work was uncommitted when they were drawn).

## Open items for the user

- Two human labellers for judge calibration (SPEC 20.2), in M8.
- Recording the 5-minute demo video, in M8.
