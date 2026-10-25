# Build log

The running record of milestone status, resolved versions, decisions and deviations. Newest entries go at the bottom of each section.

## Milestone status

| Milestone | Status | Commit | Notes |
|---|---|---|---|
| M0 Bootstrap | Done | c7bc0aa | 2026-10-22: tool images pulled. Ollama is reached at the WSL IP (the Makefile derives it). The VPN subnet clash is fixed (the Kong network moved to 172.30.0.0/16). |
| M1 Foundation | Done | a906f0a | 2026-10-25: pushed to GitHub (rebased onto the repo's initial MIT LICENSE commit). |
| M2 Graph core | Done | (this commit) | 2026-10-25: 25 graph route tests (0.8 s), the create_agent interrupt spike, 24 integration tests, the fake-model API stream e2e, and the SIGKILL resilience test (NFR-01) all pass. |
| M3 MCP and specialists | Not started | — | |
| M4 HITL and policy | Not started | — | |
| M5 Observability and cost | Not started | — | |
| M6 Long-term memory | Not started | — | |
| M7 Frontend and auth | Not started | — | |
| M8 Replay, evals, demo | Not started | — | |

## Next action

Start M3 (MCP tools and real specialists): SPEC 12, 15.2, 15.3, 16, plus amendment A1/A2 model routing. Replace `StubSpecialistRunner` with `create_agent` specialists. The spike `tests/graph/test_spike_agent_interrupt.py` already proves the interrupt/resume pattern they rely on.

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

## Open items for the user

- Two human labellers for judge calibration (SPEC 20.2), in M8.
- Recording the 5-minute demo video, in M8.
