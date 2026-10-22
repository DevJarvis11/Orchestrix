# Build log

The running record of milestone status, resolved versions, decisions and deviations. Newest entries go at the bottom of each section.

## Milestone status

| Milestone | Status | Commit | Notes |
|---|---|---|---|
| M0 Bootstrap | In progress | — | Windows-session steps done 2026-10-21 (SPEC.md, CLAUDE.md, docs). WSL-session steps pending. |
| M1 Foundation | Not started | — | |
| M2 Graph core | Not started | — | |
| M3 MCP and specialists | Not started | — | |
| M4 HITL and policy | Not started | — | |
| M5 Observability and cost | Not started | — | |
| M6 Long-term memory | Not started | — | |
| M7 Frontend and auth | Not started | — | |
| M8 Replay, evals, demo | Not started | — | |

## Next action

Resume at **M0, WSL-session steps** (see `docs/IMPLEMENTATION_PLAN.md`):
1. `git init` (local only, no remote). The first commit holds SPEC.md, CLAUDE.md and docs/.
2. Install pnpm standalone into `~/.local`; `uv tool install pre-commit`; verify `npm --version`.
3. `docker pull` the opa, gitleaks and trivy images.
4. Confirm the Ollama override is active (`systemctl show ollama -p Environment`). Then check a container can reach Ollama: `docker run --rm curlimages/curl -s http://host.docker.internal:11434/api/tags`, then the WSL IP from `hostname -I`. Record which URL works as `OLLAMA_BASE_URL`.
5. Record resolved tool versions below, mark M0 done, then start M1.

## Environment facts (from the 2026-10-21 prerequisite check)

- Ubuntu 22.04.5 on WSL2 (kernel 6.6.114.1), 32 vCPU, WSL memory 11 GB plus 8 GB swap (`.wslconfig`), host RAM 16 GB.
- Docker 29.8.2, Compose v5.6.0, through Docker Desktop with WSL integration. The NVIDIA runtime is available.
- uv 0.12.0, with CPython 3.13.14 installed by uv. The system python3 is 3.10.12 (don't use it).
- Node v26.8.1 at `~/.local/bin/node`. pnpm is not installed.
- Ollama is host-native (`/usr/local/bin/ollama`) with `qwen3:8b` (Q4_K_M, 40,960 context, tools and thinking) and `mxbai-embed-large` (1024 d, 512 context). The GPU is an RTX 4060 Laptop with 8 GB.
- The user's containers `kong-gateway` and `kong-database` hold ports 5432, 8000, 8001, 8443 and 8444. Don't touch them.
- git 2.34.1, user `ankith` <ankithm111@gmail.com>.

## Resolved versions

_(filled in as dependencies are installed)_

## Decisions and deviations

- 2026-10-21: The user approved the plan's 13 improvements over the spec (see the plan). These take precedence over SPEC.md where they conflict.
- 2026-10-21: Fully local profile, with no Tavily or Anthropic keys. Web search uses SearXNG plus fixtures.
- 2026-10-21: Claude continues between milestones without waiting and reports after each one.

## Open items for the user

- Two human labellers for judge calibration (SPEC 20.2), in M8.
- Recording the 5-minute demo video, in M8.
