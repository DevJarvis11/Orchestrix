# Run Orchestrix locally

Step-by-step guide for running the whole platform on one machine (WSL Ubuntu 22.04, as built). Every command runs from the repository root, `~/maop`.

## 1. Prerequisites (once)

| Tool | Version used | Check |
|---|---|---|
| Docker Desktop with WSL integration on | Docker 29, Compose v5 | `docker compose version` |
| uv | 0.12 | `uv --version` |
| Python | 3.13, installed by uv (never the system python3) | `uv run python --version` |
| Node.js and pnpm | Node 26, pnpm 12 | `node --version && pnpm --version` |
| VPN to the Yotta model gateway | needed for the default models | see step 3 |

RAM: the core stack needs about 1.5 GB. The observability and auth profiles add about 3 GB more, so start them only when you need them.

## 2. Install dependencies

```bash
uv sync --all-packages --frozen     # Python workspace (apps, packages, MCP servers)
pnpm install --frozen-lockfile      # frontend
make env                            # writes .env with random secrets (never overwrites values)
```

Then open `.env` and set your model gateway key:

```bash
REMOTE_LLM_API_KEY=<your Yotta key>
```

`.env` is gitignored and must never be committed.

## 3. Choose the models (the only model switch)

Agents never name a model: they ask for a role (`planner`, `specialist`, `reviewer`, `fast`). Two places decide which model serves each role:

1. **`infra/litellm/config.yaml`** lists every model the gateway can serve, with its provider and price. To add a model, add an entry there.
2. **`ORCHESTRIX_DEFAULT_MODELS`** in `.env` maps each role to one of those entries:

   ```bash
   ORCHESTRIX_DEFAULT_MODELS={"planner":"gpt-oss-120b","specialist":"gpt-oss-120b","reviewer":"gpt-oss-120b","fast":"gpt-oss-120b"}
   ```

   Change a value, restart the stack (`make dev`), and every run, script and eval uses the new model. Admins can also override per role in the Admin screen, and a task can override per role when it is submitted.

`ORCHESTRIX_LOCAL_FALLBACK=false` (the current setting) uses only the VPN models. Set it to `true` to let the gateway fall back to a host Ollama `qwen3:8b` when the VPN is down.

Turn the VPN on before you start runs, then check every model:

```bash
make gateway-check        # chat, structured output and a tool call per model, plus embed and rerank
```

## 4. Start the stack

```bash
make dev        # core profile: Postgres, Redis, S3, OPA, LiteLLM, API, worker, scheduler, 5 MCP servers, SearXNG
make seed       # demo users in two teams, the showcase lesson memory, demo_data tables
```

`make dev` builds the images, runs the database migrations and waits until every service is healthy. If an image build fails with `error getting credentials`, run it again: it is an intermittent Docker Desktop credential-helper error.

| Service | URL |
|---|---|
| API (OpenAPI docs at `/docs`) | http://localhost:58100 |
| LiteLLM gateway | http://localhost:54000 |
| S3 console (MinIO) | http://localhost:59001 |
| SearXNG | http://localhost:58888 |

## 5. Start the web app

```bash
make web                                    # http://localhost:5173, or the printed WSL address from Windows
make dev-token                             # prints a dev login token (paste it on the login screen)
make dev-token EMAIL=bob@orchestrix.local TEAM=platform ROLES=requester,reviewer
```

Demo users (dev mode): alice (requester, platform), bob (reviewer, platform), carol (reviewer, research), dave (requester, research), oscar (operator), ada (admin). A requester cannot approve their own escalation, so approve as a different user.

## 6. Run the showcase

```bash
uv run python scripts/showcase.py          # SPEC 5.6: uploads the CSV, submits the request, streams events
```

It signs in as alice to submit and as bob to answer approvals (including the email approval with an edited recipient list), and prints the live event feed. `make demo` runs the same thing with recorded web fixtures and no web access.

## 7. Optional profiles

```bash
make obs        # + Langfuse http://localhost:3100, Prometheus http://localhost:59090, Grafana http://localhost:3101 (tracing on)
make dev-full   # + Keycloak http://localhost:8180 as well
```

To log in with Keycloak instead of dev tokens, set `ORCHESTRIX_AUTH_MODE=oidc` in `.env`, run `make dev-full`, and start the web app with `VITE_AUTH_MODE=oidc`. Demo users share the password `KC_DEMO_PASSWORD` from `.env`. Langfuse and Grafana admin passwords are in `.env` too.

## 8. Checks

| Command | What it runs | Needs |
|---|---|---|
| `make test` | unit and graph tests | nothing |
| `make test-integration` | integration tests with throwaway Postgres and Redis containers | Docker |
| `make opa-test` | Rego policy tests | Docker |
| `make lint` / `make typecheck` | ruff, Biome, pyright (strict), tsc | nothing |
| `make test-stack` | live sandbox isolation and MCP checks | `make dev` |
| `make test-resilience` | kills a worker mid-run and checks another one finishes it | Docker |
| `make ci` | everything CI runs | Docker |
| `make eval-smoke` | eval smoke suite (10 golden tasks x 3 runs) | `make dev`, VPN |
| `make scan` | Trivy and gitleaks | Docker |

## 9. Stop

```bash
make down       # stops every profile; data volumes are kept
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| A run fails with `429 all_deployments_in_cooldown` | The VPN dropped and the gateway benched the model for 5 minutes. Restore the VPN, or wait, then resubmit. |
| `make gateway-check` times out on the VPN model | The VPN is off. Turn it on; with `ORCHESTRIX_LOCAL_FALLBACK=true` runs fall back to Ollama instead. |
| A tool call is always denied with "unknown tool" | OPA restarted and lost the tool registry. The worker re-pushes it on the next call; restarting the worker also pushes it. |
| Ports already in use | The stack uses 55432, 56379, 59000-59001, 58181, 54000, 58100, 58888 (and 3100, 3101, 59090, 8180 for the optional profiles). It never uses 5432, 8000, 8001, 8443 or 8444. |
