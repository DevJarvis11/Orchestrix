# Orchestrix: instructions for Codex

SPEC.md is the source of truth. Read the section for the current milestone before writing code.

At the start of every session, read `docs/IMPLEMENTATION_PLAN.md` (the approved local build plan) and `docs/BUILD_LOG.md` (current milestone, resolved versions, decisions, deviations). Update BUILD_LOG at the end of every milestone and whenever a decision or deviation is made.

Where the approved plan's "Improvements over the spec" conflict with SPEC.md, the plan wins (the user approved them). Any other conflict: stop and ask.

## Build rules (from SPEC Section 0)

- Work milestone by milestone (SPEC Section 22, local scope in the plan). Finish, test and commit one before starting the next.
- Pin every dependency to the latest stable version at install time (`uv add`, `pnpm add`), then commit the lockfiles. Do not hand-type version numbers from memory; resolve them. Versions in SPEC Section 7 are minimum floors.
- Every LLM output that drives control flow is a Pydantic model validated with structured output. No string parsing of free text.
- Every node in the agent graph is idempotent. Code before an `interrupt()` re-runs on resume, so writes use upserts keyed by IDs.
- No secrets in code or git. All config through environment variables loaded by `pydantic-settings`.
- Type-check (pyright strict on `apps/api` and `packages/*`), lint (ruff), and test (pytest) must pass before every commit.
- Every new tool, endpoint and graph node ships with tests in the same commit.
- Prefer the boring, documented API of a library over clever workarounds. If a library API is unclear, read its installed source (`.venv/lib/python3.13/site-packages/...`) or official docs; do not guess.
- When a spec detail is ambiguous, stop and ask rather than invent.

## Local environment rules

- **Everything runs inside WSL Ubuntu 22.04.** Repo at `~/maop`. Never install or run project tooling on the Windows side.
- **Git is local only.** No remote, no push. One commit per milestone (more if useful), ending with the `Co-Authored-By` trailer from the session's attribution reminder.
- **Do not touch the user's Kong containers** (`kong-gateway`, `kong-database`). They hold host ports 5432, 8000, 8001, 8443 and 8444.
- **Host port map:** Postgres 55432, Redis 56379, S3 59000/59001, OPA 58181, LiteLLM 54000, API 8100, web 5173, SearXNG 58888, Keycloak 8180, Langfuse 3100, Grafana 3101, Prometheus 59090. MCP servers stay internal (optional debug ports 59101–59105).
- **RAM is tight:** a 16 GB host with WSL capped at 11 GB. Every Compose service gets a memory limit. Run the `observability` and `auth` profiles only when needed. One worker in dev.
- **Models:** host-native Ollama (`qwen3:8b`, `mxbai-embed-large`) on an RTX 4060 with 8 GB. Fully local profile with no cloud keys. `mxbai-embed-large` has a 512-token input limit.
- **Toolchain:**
  - Python 3.13 via uv; never use the system python3 (3.10).
  - Node 26 in `~/.local/bin`, with pnpm installed standalone (no corepack).
  - `opa`, `gitleaks` and `trivy` run from Docker images through make targets.
  - `pre-commit` comes from `uv tool`.
- **Web search** is SearXNG (self-hosted) plus recorded fixtures. There is no Tavily key.
- **Deployment** (Helm, kind, release.yml, GHCR) is deferred until the user asks.

## Quick commands

Filled in during M1. See the Makefile: `make dev`, `make migrate`, `make test`, `make ci`, `make gateway-check`.
