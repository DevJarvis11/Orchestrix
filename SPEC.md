# Orchestrix — Multi-Agent Orchestration Platform: Functional & Technical Specification

Oct 21, 2026 · @Ankith

> Repo note: this file is the source of truth (see CLAUDE.md). Local build decisions and deviations are tracked in `docs/IMPLEMENTATION_PLAN.md` and `docs/BUILD_LOG.md`.

## 0. How to use this document with Claude Code

Orchestrix is a multi-agent orchestration platform: a supervisor agent plans complex tasks, specialist agents execute them with tools, a reviewer agent checks the work, memory persists across tasks, and humans approve risky steps. Every decision is traced and replayable. This one document is both the functional spec (Part A, what it does) and the technical spec (Part B, how it is built). Hand it to Claude Code as the single source of truth.

**Setup for Claude Code**

1. Export this doc as Markdown and save it in the repo root as `SPEC.md`.
2. Create `CLAUDE.md` in the repo root containing the build rules below, plus the line: "SPEC.md is the source of truth. Read the section for the current milestone before writing code."
3. Drive the build one milestone at a time using the prompts in Section 22. Never ask Claude Code to "build everything" in one go.
4. After each milestone, run the milestone's acceptance checks yourself before moving on.

**Build rules (copy into CLAUDE.md)**

- Work milestone by milestone (Section 22). Finish, test and commit one before starting the next.
- Pin every dependency to the latest stable version at install time (`uv add`, `pnpm add`), then commit the lockfiles. Do not hand-type version numbers from memory; resolve them. Versions in Section 7 are minimum floors verified as of October 2026.
- Every LLM output that drives control flow is a Pydantic model validated with structured output. No string parsing of free text.
- Every node in the agent graph is idempotent. Code before an `interrupt()` re-runs on resume, so writes use upserts keyed by IDs.
- No secrets in code or git. All config through environment variables loaded by `pydantic-settings`.
- Type-check (pyright strict on `apps/api` and `packages/*`), lint (ruff), and test (pytest) must pass before every commit.
- Every new tool, endpoint and graph node ships with tests in the same commit.
- Prefer the boring, documented API of a library over clever workarounds. If a library API is unclear, read its installed source or official docs; do not guess.
- When a spec detail is ambiguous, stop and ask rather than invent.

**Conventions used in this doc**

- `FR-xx` = functional requirement, `NFR-xx` = non-functional requirement. Tests reference these IDs.
- `M1`–`M8` = build milestones (Section 22).
- Code blocks are reference skeletons: correct in shape, to be completed and tested by Claude Code.

# Part A — Functional Specification

## 1. Product overview, goals and success metrics

Orchestrix turns one complex request ("Research the top 5 open-source vector databases, compare their licensing and benchmarks, and write a 2-page recommendation") into a planned, executed, reviewed and human-approved deliverable, with a full audit trail of every agent decision.

**Problem it solves.** Single-agent demos fail on long, multi-step work: they lose context, call the wrong tools, cannot be paused for approval, and leave no explanation of what happened. Orchestrix is production infrastructure for autonomous workflows: durable execution, typed plans, tool governance, memory, human approval gates, and observability.

**Goals (v1.0)**

1. Decompose a natural-language task into a validated execution plan (a DAG of subtasks with dependencies).
2. Execute subtasks with specialist agents that call tools through MCP, in parallel where dependencies allow.
3. Validate every deliverable with a reviewer agent; send rejected work back with feedback.
4. Pause for human input at defined escalation points and resume exactly where it stopped, even after a restart or days later.
5. Remember what worked across tasks and use it in future planning.
6. Trace, cost-account and replay every run.

**Non-goals (v1.0)**

- Not a general chat assistant or a no-code agent builder.
- No model training or fine-tuning.
- No autonomous actions on real external systems (email send, payments). These are simulated tools behind approval gates.
- No multi-tenant SaaS billing. One organization, many users.

**Success metrics (measured by the eval suite in Section 20)**

| Metric | Target for v1.0 |
| --- | --- |
| Plan validity: plans that pass schema and DAG checks | ≥ 98% |
| Task success rate on the golden task set | ≥ 80% |
| Reviewer catch rate on planted defective outputs | ≥ 85% |
| Escalation precision: escalations a human agrees were needed | ≥ 75% |
| Resume correctness after a killed worker | 100% of test cases |
| Unsafe tool calls executed without approval | 0 |
| Orchestration overhead (excluding LLM and tool time) | ≤ 200 ms per step at p95 |
| Every run has a complete trace and cost record | 100% |

## 2. Personas, roles and permissions

Four roles, enforced through Keycloak realm roles mapped into JWT claims and checked on every API call.

| Role | Who | Can do | Cannot do |
| --- | --- | --- | --- |
| `requester` | Analyst or engineer submitting work | Create tasks, watch progress, answer clarifying questions on own tasks, view own traces and memory | Approve escalations, change policies, see other users' tasks |
| `reviewer` | Team lead or domain expert | Everything a requester can, plus approve, modify, reject or take over escalations assigned to their team; view all team tasks | Change policies, tools or model routing |
| `operator` | Platform engineer on call | View all tasks, traces and costs; cancel or retry tasks; replay runs; manage tool registry and budgets | Approve escalations on behalf of reviewers (separation of duties) |
| `admin` | Platform owner | Everything, including policies, role assignment, model routing and memory deletion requests | Bypass audit logging (nothing can) |

**Permission rules**

- A user never approves an escalation on a task they requested (four-eyes rule, enforced in the policy engine).
- Every write action (approval, cancel, policy change, memory delete) writes an `audit_log` row with actor, action, target, before/after and reason.
- Memory is scoped per user and per team. A requester sees only their own long-term memories; reviewers see team memories.

## 3. Core concepts and glossary

| Term | Meaning |
| --- | --- |
| Task | One user request and everything produced to fulfil it. Has a lifecycle status (Section 4, FR-01). |
| Run | One execution attempt of a task's graph. A task has one main run and zero or more replay runs. A main run owns one LangGraph thread; a replay run forks that thread from a past checkpoint. Tracked by `thread_id`. |
| Plan | The supervisor's typed output: an ordered DAG of subtasks with dependencies, assigned specialists, inputs and expected output formats. Versioned; a re-plan creates plan v2. |
| Subtask | One unit of work in a plan, executed by exactly one specialist. |
| Specialist | A tool-using agent that owns one domain: `research`, `data_analyst`, `writer`, `coder`. |
| Supervisor | The agent that plans, dispatches subtasks, handles failures and synthesises the final deliverable. |
| Reviewer | The agent that scores each subtask output and the final deliverable against a rubric and approves or rejects. |
| Tool | A capability exposed by an MCP server (web search, fetch, code sandbox, SQL query, file store, simulated email). |
| Escalation | A pause in execution that needs a human, raised by a trigger (FR-40). Has a level: notify, approve action, approve plan, take over. |
| Working memory | Per-run shared state: plan, subtask outputs, scratch notes, errors. Lives in the LangGraph checkpoint. |
| Long-term memory | Cross-task knowledge (episodes, lessons, user preferences, domain facts) stored with embeddings and retrieved during planning. |
| Checkpoint | A persisted snapshot of graph state after a step. Enables resume, time-travel and replay. |
| Trace | The full tree of spans for a run: every LLM call, tool call, decision, memory read and escalation, with tokens, latency and cost. |
| Budget | Hard caps per run on tokens, cost (USD), wall-clock time, tool calls and graph steps. |
| Policy | A rule evaluated before a tool call or state change (allow, deny, require approval). Written in Rego, evaluated by OPA. |

## 4. Functional requirements

Priority: **P0** = required for the v1.0 demo, **P1** = required for v1.0 release, **P2** = stretch. Each FR is covered by at least one automated test named after its ID.

### 4.1 Task intake and lifecycle

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-01 | A task has status `queued → planning → awaiting_plan_approval? → running → awaiting_human? → reviewing → completed` plus terminal `failed`, `cancelled`, `budget_exceeded`. Transitions are validated by a state machine; illegal transitions raise. | P0 |
| FR-02 | Users submit a task with: title, description (max 8,000 chars), optional attachments (max 5 files, 10 MB each: pdf, md, txt, csv, xlsx), optional budget override (≤ team cap), and optional `require_plan_approval` flag. | P0 |
| FR-03 | Submission returns `task_id` and `run_id` immediately (HTTP 202). Execution is asynchronous on workers. | P0 |
| FR-04 | Users can cancel a running task. Cancellation stops after the current step and marks open tool calls cancelled. | P0 |
| FR-05 | Submitting the same request with the same `Idempotency-Key` header within 24 h returns the original task. | P1 |
| FR-06 | Live progress streams to the UI over SSE (Section 17): plan created, subtask started/finished, tool calls, review verdicts, escalations, tokens and cost. | P0 |

### 4.2 Planning (supervisor)

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-10 | The supervisor produces a `Plan` (schema in Section 11): 1–12 subtasks, each with id, description, specialist, depends_on, inputs, expected output format, acceptance criteria and complexity (`low`/`medium`/`high`). | P0 |
| FR-11 | Plans are validated deterministically before execution: schema valid, DAG acyclic, every dependency exists, every specialist exists, every referenced tool is allowed for that specialist. Invalid plans are repaired by one re-prompt with the validation errors; a second failure escalates. | P0 |
| FR-12 | Before planning, the supervisor retrieves relevant long-term memories (FR-52) and includes them in the planning prompt with citations to memory IDs. | P0 |
| FR-13 | The supervisor reports a plan confidence (0–1) with a one-line justification. Below the configured threshold (default 0.6), it escalates at level `approve_plan`. | P0 |
| FR-14 | Re-planning: after a subtask fails twice or the reviewer rejects twice, the supervisor may revise the remaining plan once (plan v2), preserving completed subtasks. | P1 |
| FR-15 | If the request is ambiguous, the supervisor may ask the requester up to 3 clarifying questions in one batch before planning (escalation level `clarify`). | P1 |

### 4.3 Execution (specialists and tools)

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-20 | Subtasks with no unmet dependencies run in parallel (max concurrency per run, default 4). | P0 |
| FR-21 | Each specialist runs a bounded tool-calling loop (max 8 tool calls per subtask, default) and returns a typed `SubtaskResult`: output, artifacts, sources, tool calls made, self-reported confidence, and notes. | P0 |
| FR-22 | Specialists can only call tools on their allow-list (Section 12). The orchestrator enforces this, not the prompt. | P0 |
| FR-23 | Every tool call passes the policy check (FR-90) before execution and writes a `tool_calls` row with input, output (truncated to 32 KB, full payload in object storage), latency, status and cost. | P0 |
| FR-24 | Tool errors are classified as retryable (timeout, 429, 5xx) or not (validation, permission). Retryable errors retry with exponential backoff and jitter (3 attempts). A subtask that fails twice is retried once with a different approach hint, then escalates. | P0 |
| FR-25 | Tool outputs are treated as untrusted data: wrapped in delimiters, never executed as instructions, and scanned for prompt-injection patterns (Section 15). | P0 |
| FR-26 | Code execution runs only in the sandbox (Section 12): no network, read-only base image, CPU, memory and time limits. | P0 |

### 4.4 Review

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-30 | The reviewer scores every subtask result against its acceptance criteria and a standard rubric (correctness, completeness, groundedness in sources, format). Output: `Review` with verdict (`approve`/`revise`/`escalate`), score 0–10, issues list with severity, and feedback. | P0 |
| FR-31 | A `revise` verdict routes back to the same specialist with the feedback. Max 2 revisions per subtask; then escalate. | P0 |
| FR-32 | The reviewer uses a different model from the specialist that produced the work (configurable), to avoid self-grading bias. | P1 |
| FR-33 | The final synthesised deliverable gets a final review before delivery. | P0 |
| FR-34 | Claims in research and writing outputs must cite sources returned by tools. The reviewer flags any claim without a source as an issue of severity `high`. | P1 |

### 4.5 Human-in-the-loop

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-40 | Escalation triggers: plan confidence below threshold; a subtask failing twice; reviewer score below threshold (default 6/10) after max revisions; a tool marked `sensitive` (simulated email send, file delete, SQL write, any external POST); budget at 80% (notify) or 100% (approve to continue); explicit user request; policy decision `require_approval`. | P0 |
| FR-41 | Escalation levels: `notify` (continue, inform), `clarify` (ask the requester questions), `approve_action` (approve one tool call), `approve_plan` (approve the whole plan), `take_over` (human writes the output, agent stands down for that subtask). Trigger-to-level mapping is configurable. | P0 |
| FR-42 | On escalation the run pauses via LangGraph `interrupt()` with a JSON payload: task context, plan, completed steps, the pending decision, the agent's proposed action and reasoning, and related memories. | P0 |
| FR-43 | Reviewers act with: approve, approve with edits (edit the proposed tool args or plan), reject with reason (agent must try an alternative or fail the subtask), take over (provide output). The run resumes with `Command(resume=...)`. | P0 |
| FR-44 | Reviewers can chat with the agent about a pending escalation (read-only questions answered from run state) before deciding. | P1 |
| FR-45 | Escalations not acted on within the SLA (default 24 h) notify again; after 72 h the task moves to `failed` with reason `escalation_timeout`. | P1 |
| FR-46 | Notifications for new escalations go to the in-app inbox and to Slack (incoming webhook) with a deep link. | P1 |

### 4.6 Memory

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-50 | Working memory (plan, results, scratch, errors) is part of the checkpointed graph state, scoped to one run. | P0 |
| FR-51 | After a task completes, a memory-writer step extracts up to 10 long-term memories of four kinds: `episode` (task summary and outcome), `lesson` (what worked or failed and why), `preference` (stated user preferences only), `fact` (domain facts with source). Each has text, kind, importance 0–1, namespace, source run id and embedding. | P0 |
| FR-52 | Retrieval for planning: hybrid search (vector similarity + keyword) over the user's and team's namespaces, top 8 after reranking, filtered by kind and recency. | P0 |
| FR-53 | Memory scoring: importance decays over time unless the memory is retrieved and used; access count and last-used time are tracked. | P1 |
| FR-54 | Consolidation job (nightly): merges near-duplicate memories (cosine ≥ 0.92) into one summarised memory, keeping links to originals. | P1 |
| FR-55 | Users can view, edit and delete their memories in a Memory page. Delete is a hard delete of the memory and its embedding, logged in the audit log. Admins can process a full-user data deletion request. | P0 |
| FR-56 | Memories are never written from tool output alone: a `fact` must cite a source; a `preference` must come from the user's own words. | P0 |

### 4.7 Observability, cost and replay

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-60 | Every run emits OpenTelemetry spans for graph nodes, LLM calls, tool calls, memory reads/writes, policy checks and escalations, exported to Langfuse. | P0 |
| FR-61 | The in-app Trace Explorer shows the run as a graph (supervisor → specialists → reviewer) with status colours; clicking a node shows prompt, response, tool I/O, tokens, latency and cost. | P0 |
| FR-62 | Per-run cost record: tokens by agent and model, tool calls, wall-clock time, human wait time and USD cost. Aggregates by task type, agent and model on a Costs page. | P0 |
| FR-63 | Budgets per run (defaults): 400k tokens, USD 2.00, 30 min active time (excluding human wait), 60 tool calls, 150 graph steps. Hitting a cap triggers FR-40. | P0 |
| FR-70 | Replay: an operator selects any checkpoint of a past run, optionally edits the state at that point, and re-runs from there as a new replay run. Original runs are never mutated. | P1 |
| FR-71 | Replay diff: side-by-side comparison of original vs replay (plan, outputs, review scores, cost). | P2 |
| FR-72 | Prometheus metrics and Grafana dashboards for platform health (Section 19). | P1 |

### 4.8 Governance and administration

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-90 | A policy engine (OPA) evaluates every tool call and every escalation decision with inputs: user, roles, team, tool, args summary, specialist, task risk. Results: `allow`, `deny` (with reason) or `require_approval`. | P0 |
| FR-91 | Admins manage tools (enable, disable, mark sensitive, set rate limits), model routing and budgets through the API and Admin UI, without redeploying. | P1 |
| FR-92 | Immutable audit log of all human and admin actions, queryable and exportable as CSV. | P0 |
| FR-93 | PII redaction (emails, phone numbers, card-like numbers, government ID patterns) on content before it is written to long-term memory and to traces, configurable per pattern. | P1 |

## 5. User journeys and showcase demo

Five journeys define v1.0; the showcase demo (5.6) exercises all of them in one 5-minute recording.

### 5.1 Requester submits and watches a task

1. Requester opens **New Task**, enters the request, attaches a CSV, submits.
2. Task page opens instantly with status `planning` and a live event feed.
3. The plan appears as a DAG; subtasks light up as they start and finish.
4. The final deliverable (Markdown report with citations) appears with the reviewer's score; requester downloads it.

### 5.2 Reviewer handles an escalation

1. Slack and the in-app inbox show "Approval needed: send summary email to 3 recipients (task #182)".
2. Reviewer opens the escalation: sees the task, plan progress, the proposed tool call with arguments, the agent's reasoning and related past decisions.
3. Reviewer asks the agent "why these recipients?" in the side chat and gets an answer from run state.
4. Reviewer edits one recipient, approves. The run resumes within 2 seconds and the event feed shows `resumed by <reviewer>`.

### 5.3 Supervisor asks for plan approval

1. A vague high-cost task produces a plan with confidence 0.45.
2. Run pauses at `awaiting_plan_approval`; the reviewer sees the plan, estimated cost and the low-confidence reason.
3. Reviewer removes one subtask and approves; execution starts on the edited plan (plan v1 edited, stored with editor and diff).

### 5.4 Operator debugs a failure

1. A task ends `failed`. Operator opens **Trace Explorer**: the writer node is red after two reviewer rejections.
2. Operator clicks the node, sees the reviewer's issue ("claim without source") and that research returned only 1 source.
3. Operator opens **Replay**, picks the checkpoint before the research subtask, raises its `max_tool_calls` from 8 to 12, and replays. The replay succeeds; the diff view shows research returned 4 sources.

### 5.5 User manages memory

1. User opens **Memory**, sees 23 memories grouped by kind, each with its source task.
2. User deletes one wrong preference. It disappears from search immediately; the audit log records the deletion.
3. Next similar task: the planning prompt shows the retrieved lessons and the plan reuses a previously successful structure.

### 5.6 Showcase demo scenario (scripted, seeded, reproducible)

**Request:** "Compare the 4 most popular open-source vector databases on license, GitHub activity and published benchmark claims. Analyse the attached CSV of our query latencies. Write a 2-page recommendation, then email the summary to the platform team."

What the demo must show, in order:

1. Memory informing planning: a seeded lesson ("benchmarks need the vendor's own source plus one independent source") is retrieved and visible in the plan rationale.
2. Supervisor decomposes into 5 subtasks: research (×2 in parallel), data analysis of the CSV (sandboxed Python), writing, email.
3. Parallel execution with live tool calls in the feed.
4. Reviewer rejects the first draft for an unsourced benchmark claim; the writer revises; the reviewer approves (score ≥ 8).
5. The simulated email tool is `sensitive`, so the run pauses for `approve_action`; a different user approves with an edited recipient list.
6. Delivery, then the memory writer saves new lessons; the Memory page shows them.
7. Trace Explorer walk-through with cost per agent, and one replay from a checkpoint.

`make demo` seeds users, memories, the CSV and a recorded web-search fixture so the demo runs identically offline.

## 6. Non-functional requirements

| ID | Area | Requirement |
| --- | --- | --- |
| NFR-01 | Durability | A worker killed mid-run (SIGKILL) resumes the run from the last checkpoint on another worker with no duplicated side effects. Checkpointer durability mode: `sync`. |
| NFR-02 | Latency | API p95 < 150 ms for non-LLM endpoints. Resume after approval starts executing within 2 s. SSE event delivery < 500 ms after the event is written. |
| NFR-03 | Throughput | 20 concurrent runs on a single dev machine with local models queued; 200 concurrent runs on the K8s deployment with 4 worker replicas (LLM throughput is the limit, not orchestration). |
| NFR-04 | Availability | API and workers are stateless and horizontally scalable; all state in PostgreSQL and Redis. Graceful shutdown drains in-flight steps (SIGTERM, 30 s). |
| NFR-05 | Security | OIDC login, JWT validation on every request, RBAC, policy check on every tool call, sandboxed code, secrets only from env/secret store, TLS at the ingress, OWASP ASVS L2 baseline for the API. |
| NFR-06 | Privacy | PII redaction before memory and trace persistence; hard delete of user memories; data retention defaults: traces 30 days, runs 180 days, audit log 1 year. |
| NFR-07 | Cost | Every run accounted to USD within ±5% of provider pricing; budgets enforced before each LLM call using estimated tokens. |
| NFR-08 | Portability | Runs fully offline on local models (Ollama) and on any OpenAI-compatible endpoint; switching providers is config only. |
| NFR-09 | Observability | 100% of runs traced; logs are structured JSON with `trace_id`, `run_id`, `task_id` on every line. |
| NFR-10 | Quality gates | CI blocks merges when unit/integration tests fail, type check fails, or the eval smoke suite drops task success by > 5 points vs `main`. |
| NFR-11 | Accessibility | UI meets WCAG 2.2 AA: keyboard navigable, visible focus, colour never the only status signal. |
| NFR-12 | Maintainability | Python test coverage ≥ 80% on `packages/core` and `packages/agents`; every module has a README stating its responsibility. |

# Part B — Technical Specification

## 7. Tech stack

The stack is LangGraph on PostgreSQL for durable orchestration, MCP for tools, LiteLLM as the model gateway, OPA for policy, and OpenTelemetry into Langfuse for traces. Every component is open source and self-hostable, so the whole platform runs offline on local models and moves to cloud models by config alone. Version floors were checked in October 2026; Claude Code resolves the latest stable release at install and commits lockfiles.

### 7.1 Backend and orchestration

| Layer | Choice | Version floor | Why this choice |
| --- | --- | --- | --- |
| Language | Python | 3.13 | Mature async, full library support. |
| Package and env manager | uv (workspace mode) | latest | Fast, reproducible lockfile, monorepo workspaces. |
| API framework | FastAPI + Uvicorn | latest | Async-native, OpenAPI generated from Pydantic models. |
| Data validation | Pydantic v2 + pydantic-settings | 2.x latest | Typed schemas for every LLM output, API body and config. |
| Agent orchestration | LangGraph | ≥ 1.2 | Stable 1.x with durable state, checkpoints, `interrupt()` HITL and time-travel. [source](https://changelog.langchain.com/announcements/ann_lxOZRCoFw2Qn9) |
| Specialist agent loop | LangChain `create_agent` | ≥ 1.0 | Replaces the deprecated `langgraph.prebuilt`; middleware hooks for budgets, policy and HITL. |
| Checkpointer | `langgraph-checkpoint-postgres` (`AsyncPostgresSaver`) | latest | Durable checkpoints in the same Postgres. Durability mode `sync`. [source](https://docs.langchain.com/oss/python/langgraph/durable-execution) |
| Long-term memory store | Own `memories` table: pgvector HNSW + Postgres full-text (Section 13) | latest | Hybrid search (vector + keyword), hard delete, decay and audit need full control of the table; LangGraph's built-in store offers only semantic search. |
| ORM and migrations | SQLAlchemy 2.0 (async) + Alembic | 2.0.x latest | Typed models for app tables; Alembic owns app schema. |
| Postgres driver | psycopg 3 (`psycopg[binary,pool]`) | 3.2+ | One driver for SQLAlchemy (`postgresql+psycopg`) and the LangGraph checkpointer. |
| Task workers | Taskiq + taskiq-redis (Redis Streams broker) | latest | Async-native workers that run graph steps; acknowledged delivery. Run leases in Postgres guarantee recovery (Section 8). |
| HTTP client | httpx | latest | Async, timeouts, retries via tenacity. |
| Retries | tenacity | latest | Exponential backoff with jitter for tools and providers. |
| Logging | structlog (JSON) | latest | Structured logs carrying `trace_id`, `run_id`, `task_id`. |

### 7.2 Models, tools and policy

| Layer | Choice | Version floor | Why this choice |
| --- | --- | --- | --- |
| Model gateway | LiteLLM Proxy | latest | One OpenAI-compatible endpoint for Ollama, vLLM, Anthropic and OpenAI; virtual keys, budgets, fallbacks, Langfuse callback. |
| Local models | Ollama: `qwen3:8b` (chat, tool calling), `mxbai-embed-large` (embeddings, 1024 dims) | latest | Fits an 8 GB GPU; zero API cost for development. |
| Self-hosted large model | Any vLLM OpenAI-compatible server (e.g. a Qwen3 32B deployment) | — | Stronger planner and reviewer without cloud cost. |
| Cloud models (optional) | Anthropic Claude Sonnet 5.5 (planner/reviewer), Claude Haiku 5.5 (fast) | — | Best demo quality; swapped in via the gateway alias only. |
| Reranker | Self-hosted reranker endpoint (e.g. Qwen3 reranker on vLLM) or `BAAI/bge-reranker-v2-m3` via sentence-transformers on CPU | — | Raises memory-retrieval precision. |
| Tool protocol | MCP: official `mcp` Python SDK (FastMCP) for servers; `langchain-mcp-adapters` client; Streamable HTTP transport | latest | Standard, swappable tools; each tool server is its own container. |
| Web search | Tavily API (primary), SearXNG self-hosted (keyless fallback) | latest | Clean results with snippets; offline fixtures in tests. |
| Web fetch and extraction | httpx + trafilatura | latest | Robust main-content extraction from HTML. |
| Code sandbox | Dedicated sandbox MCP server container: non-root, read-only root fs, tmpfs workdir, no egress network, CPU/memory/time limits; gVisor `runsc` RuntimeClass on Kubernetes | — | Strong isolation without Docker-socket access from workers. |
| Policy engine | Open Policy Agent (OPA), Rego v1 | 1.x latest | Policy-as-code, tested with `opa test`, decisions logged. |
| Identity | Keycloak (OIDC) | 26.x | SSO, realm roles mapped to RBAC. |

### 7.3 Data, observability and frontend

| Layer | Choice | Version floor | Why this choice |
| --- | --- | --- | --- |
| Primary database | PostgreSQL + pgvector | PG 18, pgvector 0.8.2 (`pgvector/pgvector:pg18`) | One database for app data, checkpoints, memory vectors and full-text search. [source](https://docs.pgedge.com/pgvector/v0-8-2/additional-installation-methods/) |
| Cache, broker, pub/sub | Redis | 8.2+ | Streams for jobs, pub/sub for SSE fan-out, rate limits. |
| Object storage | S3-compatible (the store shipped in Langfuse's upstream compose for dev; AWS S3 or any S3 in prod) via aioboto3 | — | Artifacts, attachments, large tool payloads. |
| Tracing | OpenTelemetry SDK + OTLP exporter → Langfuse self-hosted | Langfuse v4 (needs ClickHouse ≥ 25.12) | Open-source LLM tracing, scores and datasets; OTLP ingestion. [source](https://railway.com/deploy/langfuse-v4-llm-observability-stack) |
| Metrics and dashboards | Prometheus + Grafana (`prometheus-fastapi-instrumentator` + custom metrics) | latest | Platform health, cost and escalation dashboards. |
| Frontend runtime | Node.js LTS + pnpm | Node 24 LTS | Current LTS line. |
| Frontend framework | React 19 + Vite + TypeScript (strict) | React 19, TS 5.x | Matches the existing skill set; fast builds. |
| Routing and data | TanStack Router + TanStack Query | latest | Type-safe routes, caching, SSE-driven invalidation. |
| UI | shadcn/ui + Tailwind CSS v4 + lucide-react | latest | Accessible primitives, consistent design. |
| Graph rendering | React Flow (`@xyflow/react`) + elkjs auto-layout | latest | Plan DAG and trace tree views. |
| API client | openapi-typescript + openapi-fetch (generated from FastAPI OpenAPI) | latest | End-to-end typed client, no hand-written types. |
| Auth client | oidc-client-ts | latest | PKCE login against Keycloak. |

### 7.4 Quality and delivery

| Layer | Choice | Why this choice |
| --- | --- | --- |
| Lint and format | ruff (Python), Biome (TypeScript) | One fast tool per language. |
| Type check | pyright (strict), `tsc --noEmit` | Catches contract drift early. |
| Tests | pytest + pytest-asyncio + testcontainers + respx; Vitest; Playwright (e2e) | Real Postgres/Redis in integration tests; mocked HTTP. |
| Evals | Custom eval harness (Section 20) + Langfuse datasets and scores | Golden tasks, LLM-as-judge with calibration, CI gate. |
| Containers | Docker multi-stage builds, distroless or slim base, non-root | Small, secure images. |
| Local orchestration | Docker Compose with profiles (`core`, `observability`, `auth`, `models`) | Start only what a milestone needs. |
| Kubernetes | Helm 3 chart; kind for local clusters; optional Argo CD | Production-shaped deployment. |
| CI/CD | GitHub Actions; Trivy image and dependency scans; pre-commit hooks | Automated gates on every PR. |

## 8. System architecture

The API and workers are stateless; PostgreSQL holds every durable fact (app data, LangGraph checkpoints, memories, the event log), so any worker can pick up any run. Redis moves jobs and live events, but losing Redis never loses work.

[embedded content: Orchestrix architecture · 6 layers, 14 components]

Reading top to bottom: users call the API; the API writes to Postgres and enqueues jobs on Redis; workers run the LangGraph graph, checkpointing to Postgres and calling models, tools and policy below them; everything emits traces and metrics.

### 8.1 Component responsibilities

| Component | Owns | Scales by |
| --- | --- | --- |
| API | Auth, validation, task creation, SSE relay, escalation decisions, admin | Replicas behind the ingress |
| Worker | Running graph steps for a run it holds a lease on; publishing events | Replicas; each runs N concurrent runs (default 4) |
| Scheduler | Lease reaper, SLA checks, memory consolidation, retention jobs | One active instance |
| MCP servers | One tool family each, behind service-token auth | Replicas per server |
| LiteLLM | Model routing, retries, per-team virtual keys and budgets | Replicas |
| OPA | Policy decisions | Sidecar or small deployment |

### 8.2 Run leases (crash recovery, NFR-01)

A run is executed by at most one worker at a time, enforced in Postgres rather than by the broker.

1. **Acquire.** `start_run` and `resume_run` jobs run `UPDATE runs SET lease_owner = :worker, lease_expires_at = now() + interval '60 seconds' WHERE id = :run AND (lease_owner IS NULL OR lease_expires_at < now()) RETURNING id`. No row back means another worker holds it: acknowledge the job and exit.
2. **Heartbeat.** Every 15 s the worker extends `lease_expires_at`. If an extension updates zero rows, the lease was lost: stop after the current step and exit without writing further.
3. **Release.** On pause (interrupt), completion, failure or cancel, set `lease_owner = NULL`.
4. **Reap.** Every 30 s the scheduler finds runs with status `running` and an expired lease, clears the lease, and enqueues `resume_run`. The job continues with `graph.astream(None, config)`, which resumes from the last checkpoint. The node that was in flight re-runs, which is why every node is idempotent (11.6).
5. **Crash loop guard.** A run reclaimed 3 times fails with reason `crash_loop` and an operator alert.

### 8.3 Event flow

Nodes emit progress through LangGraph's custom stream writer. The worker turns each update into a typed event, inserts it into `run_events`, then publishes it to Redis `run:{run_id}`. The API's SSE endpoint replays from `run_events` and then relays Redis (17.2). The same rows feed the Trace Explorer and the decision audit.

## 9. Repository structure

One monorepo: a uv workspace for Python and a pnpm workspace for the frontend. Packages hold logic; apps only wire packages together, so every agent and tool is testable without HTTP.

```text
orchestrix/
├── CLAUDE.md                     # build rules (Section 0)
├── SPEC.md                       # this document
├── pyproject.toml                # uv workspace root, ruff + pyright config
├── uv.lock
├── Makefile                      # dev, test, lint, eval, demo, migrate targets
├── .env.example
├── apps/
│   ├── api/                      # FastAPI service
│   │   └── src/orchestrix_api/
│   │       ├── main.py           # app factory, lifespan, middleware
│   │       ├── deps.py           # DB session, auth, settings injection
│   │       ├── routers/          # tasks, runs, escalations, memory, traces, admin, stream
│   │       └── security/         # JWT validation, RBAC dependency
│   ├── worker/                   # Taskiq workers
│   │   └── src/orchestrix_worker/
│   │       ├── broker.py         # RedisStreamBroker setup
│   │       ├── jobs.py           # start_run, resume_run, replay_run, memory_consolidate
│   │       ├── lease.py          # run lease + heartbeat + reaper
│   │       └── scheduler.py      # periodic jobs (reaper, consolidation, SLA checks)
│   └── web/                      # React 19 + Vite frontend
│       └── src/
│           ├── routes/           # TanStack Router file routes
│           ├── features/         # tasks, plan-view, escalations, trace-explorer, memory, costs, admin
│           ├── components/ui/    # shadcn/ui
│           └── lib/api/          # generated openapi-fetch client
├── packages/
│   ├── core/                     # domain models, settings, db, events, budgets, errors
│   │   └── src/orchestrix_core/
│   │       ├── config.py         # pydantic-settings
│   │       ├── db/               # SQLAlchemy models, session, repositories
│   │       ├── schemas/          # Plan, Subtask, SubtaskResult, Review, Escalation, events
│   │       ├── state_machine.py  # task status transitions (FR-01)
│   │       ├── budgets.py        # budget accounting and enforcement
│   │       ├── events.py         # event bus (Postgres outbox + Redis pub/sub)
│   │       └── telemetry.py      # OpenTelemetry setup, span helpers
│   ├── agents/                   # LangGraph graph and agent logic
│   │   └── src/orchestrix_agents/
│   │       ├── graph.py          # build_graph(): StateGraph wiring
│   │       ├── state.py          # RunState TypedDict + reducers
│   │       ├── nodes/            # intake, retrieve_memory, plan, validate_plan, approve_plan,
│   │       │                     # dispatch, execute_subtask, review_subtask, escalate,
│   │       │                     # synthesize, final_review, write_memory, finalize
│   │       ├── specialists/      # research, data_analyst, writer, coder (create_agent + middleware)
│   │       ├── prompts/          # versioned Jinja2 prompt templates (one file per agent)
│   │       ├── middleware/       # budget, policy, injection guard, tracing middleware
│   │       └── llm.py            # model alias -> ChatOpenAI(base_url=LiteLLM)
│   ├── memory/                   # long-term memory: write, retrieve, rerank, consolidate, decay
│   ├── tools/                    # MCP client registry, allow-lists, tool metadata
│   ├── policy/                   # OPA client + input builders
│   └── evals/                    # eval harness, golden tasks, judges, reports
├── mcp_servers/
│   ├── web/                      # web_search, web_fetch
│   ├── sandbox/                  # run_python
│   ├── data/                     # sql_query (read-only), describe_table
│   ├── files/                    # read_artifact, write_artifact, list_artifacts
│   └── comms/                    # send_email (simulated, sensitive)
├── policies/                     # Rego policies + opa tests
├── infra/
│   ├── compose/                  # docker-compose.yml + profiles, Langfuse, Keycloak realm export
│   ├── grafana/                  # dashboards as JSON
│   ├── litellm/                  # config.yaml
│   └── helm/orchestrix/          # Helm chart
├── migrations/                   # Alembic
├── tests/
│   ├── unit/  integration/  e2e/  fixtures/
└── .github/workflows/            # ci.yml, eval.yml, release.yml
```

Naming rules: Python packages are `orchestrix_<name>`; every graph node is a function `async def <name>(state: RunState, runtime: Runtime[RunContext]) -> dict` in its own module under `nodes/`; prompts live in files, never inline strings.

## 10. Data model

All application tables live in one PostgreSQL 18 database, owned by Alembic. LangGraph's checkpointer creates and owns its own tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) via `await checkpointer.setup()` on startup; Alembic never touches them. Primary keys use PostgreSQL 18's built-in `uuidv7()` so IDs sort by creation time.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- Identity (mirrors Keycloak; Keycloak is the source of truth)
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  keycloak_sub text UNIQUE NOT NULL,
  email text UNIQUE NOT NULL,
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE teams (id uuid PRIMARY KEY DEFAULT uuidv7(), name text UNIQUE NOT NULL,
  monthly_budget_usd numeric(10,2) NOT NULL DEFAULT 100);
CREATE TABLE team_members (team_id uuid REFERENCES teams, user_id uuid REFERENCES users,
  PRIMARY KEY (team_id, user_id));

-- Tasks and runs
CREATE TABLE tasks (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  team_id uuid NOT NULL REFERENCES teams,
  requester_id uuid NOT NULL REFERENCES users,
  title text NOT NULL,
  description text NOT NULL CHECK (length(description) <= 8000),
  status text NOT NULL,              -- FR-01 values, validated by state_machine.py
  require_plan_approval boolean NOT NULL DEFAULT false,
  budget jsonb NOT NULL,             -- {max_tokens, max_usd, max_seconds, max_tool_calls, max_steps}
  idempotency_key text,
  final_artifact_id uuid,
  failure_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (requester_id, idempotency_key)
);
CREATE TABLE runs (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  thread_id text NOT NULL,           -- LangGraph thread; main run: = id; replay: original run's thread
  head_checkpoint_id text,           -- latest checkpoint of THIS run's branch; all reads pin to it
  task_id uuid NOT NULL REFERENCES tasks,
  kind text NOT NULL CHECK (kind IN ('main','replay')),
  replay_of_run_id uuid REFERENCES runs,
  replay_from_checkpoint_id text,
  status text NOT NULL,              -- queued|running|paused|completed|failed|cancelled
  lease_owner text,                  -- worker id holding the run
  lease_expires_at timestamptz,      -- heartbeat-renewed; reaper re-enqueues expired
  graph_version text NOT NULL,       -- git sha of packages/agents
  started_at timestamptz, ended_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX runs_lease_idx ON runs (status, lease_expires_at);

-- Plans and subtasks
CREATE TABLE plans (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid NOT NULL REFERENCES runs,
  version int NOT NULL,
  body jsonb NOT NULL,               -- Plan schema (Section 11)
  confidence real NOT NULL,
  rationale text NOT NULL,
  memory_ids uuid[] NOT NULL DEFAULT '{}',
  edited_by uuid REFERENCES users,   -- set when a human edited it
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, version)
);
CREATE TABLE subtasks (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid NOT NULL REFERENCES runs,
  plan_version int NOT NULL,
  key text NOT NULL,                 -- 's1','s2' from the plan
  specialist text NOT NULL,
  status text NOT NULL,              -- pending|running|in_review|approved|failed|taken_over|skipped
  attempts int NOT NULL DEFAULT 0,
  revisions int NOT NULL DEFAULT 0,
  result jsonb,                      -- SubtaskResult
  UNIQUE (run_id, plan_version, key)
);
CREATE TABLE reviews (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid NOT NULL REFERENCES runs,
  subtask_id uuid REFERENCES subtasks, -- null = final review
  verdict text NOT NULL, score real NOT NULL,
  issues jsonb NOT NULL, feedback text NOT NULL,
  reviewer_model text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Tool calls and LLM calls (cost accounting)
CREATE TABLE tool_calls (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid NOT NULL REFERENCES runs,
  subtask_id uuid REFERENCES subtasks,
  idempotency_key text UNIQUE NOT NULL,  -- hash(run_id, node, step, tool, args)
  tool text NOT NULL, server text NOT NULL,
  args jsonb NOT NULL,
  output_preview text,               -- first 32 KB
  output_object_key text,            -- full payload in S3
  status text NOT NULL,              -- ok|error|denied|awaiting_approval|cancelled
  policy_decision jsonb,
  latency_ms int, error text,
  trace_id text, span_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE llm_calls (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid NOT NULL REFERENCES runs,
  agent text NOT NULL,               -- supervisor|research|data_analyst|writer|coder|reviewer|memory_writer
  model_alias text NOT NULL, model_id text NOT NULL,
  input_tokens int NOT NULL, output_tokens int NOT NULL,
  cost_usd numeric(12,6) NOT NULL,
  latency_ms int NOT NULL,
  trace_id text, span_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_run_idx ON llm_calls (run_id);

-- Human-in-the-loop
CREATE TABLE escalations (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid NOT NULL REFERENCES runs,
  interrupt_id text NOT NULL,        -- LangGraph Interrupt.id, used for targeted resume
  trigger text NOT NULL,             -- FR-40 trigger name
  level text NOT NULL,               -- notify|clarify|approve_action|approve_plan|take_over
  payload jsonb NOT NULL,            -- context shown to the reviewer
  status text NOT NULL,              -- open|resolved|expired
  assignee_team_id uuid REFERENCES teams,
  due_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, interrupt_id)
);
CREATE TABLE escalation_actions (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  escalation_id uuid NOT NULL REFERENCES escalations,
  actor_id uuid NOT NULL REFERENCES users,
  action text NOT NULL,              -- approve|approve_with_edits|reject|take_over|answer
  body jsonb NOT NULL,               -- edits, reason, takeover output, answers
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Long-term memory (Section 13)
CREATE TABLE memories (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  namespace text NOT NULL,           -- 'user:<id>' or 'team:<id>'
  kind text NOT NULL CHECK (kind IN ('episode','lesson','preference','fact')),
  text text NOT NULL,
  embedding vector(1024) NOT NULL,   -- must equal the embedding model's dimension
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  importance real NOT NULL CHECK (importance BETWEEN 0 AND 1),
  source_run_id uuid REFERENCES runs,
  source_ref text,                   -- URL or message id backing a fact/preference
  access_count int NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  consolidated_into uuid REFERENCES memories ON DELETE SET NULL, -- set on originals after merge
  derived_from uuid[] NOT NULL DEFAULT '{}',                       -- set on a consolidated summary
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memories_embedding_hnsw ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_tsv_gin ON memories USING gin (tsv);
CREATE INDEX memories_ns_kind ON memories (namespace, kind) WHERE consolidated_into IS NULL;

-- Artifacts, events, audit, tool registry
CREATE TABLE artifacts (
  id uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id uuid REFERENCES runs, task_id uuid REFERENCES tasks,
  name text NOT NULL, mime text NOT NULL, bytes bigint NOT NULL,
  object_key text NOT NULL, sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE run_events (               -- transactional outbox for SSE + audit of agent decisions
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES runs,
  type text NOT NULL,                -- Section 17 event types
  data jsonb NOT NULL,
  published boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX run_events_run_idx ON run_events (run_id, id);
CREATE TABLE audit_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id uuid REFERENCES users,    -- null = system
  action text NOT NULL, target_type text NOT NULL, target_id text NOT NULL,
  before jsonb, after jsonb, reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- audit_log is append-only: REVOKE UPDATE, DELETE ON audit_log FROM orchestrix_app;
CREATE TABLE app_meta (key text PRIMARY KEY, value jsonb NOT NULL); -- e.g. embedding_model, schema notes
CREATE TABLE tools (
  name text PRIMARY KEY,             -- e.g. 'web.search'
  server text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  sensitive boolean NOT NULL DEFAULT false,
  allowed_specialists text[] NOT NULL,
  rate_limit_per_min int NOT NULL DEFAULT 60,
  input_schema jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

**Data rules**

- The app connects as role `orchestrix_app` (no DDL rights); migrations run as `orchestrix_migrator`.
- `updated_at` is maintained by a trigger on every table that has it.
- Retention (NFR-06) is a nightly job: delete `run_events`, `llm_calls` and S3 payloads older than their window; checkpoints of runs older than 180 days are pruned with the checkpointer's `adelete_thread(run_id)`.

## 11. Agent graph design

The run is one LangGraph `StateGraph` compiled with the Postgres checkpointer. A parent graph plans, dispatches and synthesises; each subtask runs in a `subtask_worker` subgraph, fanned out in parallel with `Send`. Every pause is an `interrupt()`, and every resume is `Command(resume=...)` keyed by interrupt ID, so several parallel escalations can be answered independently.

[embedded content: Run graph · parent nodes and the parallel subtask worker]

The parent graph runs top row then bottom row; dispatch keeps sending ready subtasks to workers until all are done, then synthesis and final review produce the deliverable.

### 11.1 Core schemas (`packages/core/schemas`)

```python
from typing import Literal
from pydantic import BaseModel, Field

Specialist = Literal["research", "data_analyst", "writer", "coder"]

class PlannedSubtask(BaseModel):
    key: str = Field(pattern=r"^s\d{1,2}$")
    description: str = Field(max_length=1000)
    specialist: Specialist
    depends_on: list[str] = []
    inputs: dict[str, str] = {}          # name -> reference, e.g. "s1.output" or "attachment:latency.csv"
    expected_output: Literal["markdown", "json", "table", "code", "file"]
    acceptance_criteria: list[str] = Field(min_length=1, max_length=6)
    complexity: Literal["low", "medium", "high"]
    max_tool_calls: int = Field(default=8, ge=1, le=20)

class Plan(BaseModel):
    goal: str
    subtasks: list[PlannedSubtask] = Field(min_length=1, max_length=12)
    deliverable_format: Literal["markdown_report", "json", "table", "code"]
    confidence: float = Field(ge=0, le=1)
    confidence_reason: str
    used_memory_ids: list[str] = []
    estimated_cost_usd: float = Field(ge=0)

class SubtaskResult(BaseModel):
    key: str
    output: str                           # markdown, JSON text or code
    artifacts: list[str] = []             # artifact ids
    sources: list[str] = []               # URLs or artifact ids backing claims
    tool_call_ids: list[str] = []
    confidence: float = Field(ge=0, le=1)
    notes: str = ""

class Issue(BaseModel):
    description: str
    severity: Literal["low", "medium", "high", "critical"]
    quote: str | None = None              # exact excerpt from the output

class Review(BaseModel):
    verdict: Literal["approve", "revise", "escalate"]
    score: float = Field(ge=0, le=10)
    issues: list[Issue] = []
    feedback: str
```

### 11.2 Run state (`packages/agents/state.py`)

```python
import operator
from typing import Annotated, TypedDict
from dataclasses import dataclass

def merge_dict(left: dict, right: dict) -> dict:
    return {**left, **right}

class RunState(TypedDict, total=False):
    task: TaskInput                                   # id, title, description, attachments, flags
    memories: list[MemoryHit]
    plan: Plan
    plan_version: int
    plan_repairs: int
    plan_errors: list[str]                            # validate_plan output; empty = valid
    subtask_status: Annotated[dict[str, str], merge_dict]     # key -> pending|running|approved|failed|taken_over|skipped
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dict]
    reviews: Annotated[dict[str, list[Review]], merge_dict]
    deliverable: str
    final_review: Review
    final_revisions: int
    usage: Annotated[Usage, add_usage]                # tokens, usd, tool_calls, steps (custom reducer)
    errors: Annotated[list[ErrorRecord], operator.add]
    escalations: Annotated[list[EscalationRecord], operator.add]

@dataclass(frozen=True)
class RunContext:                                     # immutable per-run dependencies, not checkpointed
    run_id: str
    task_id: str
    user_id: str
    team_id: str
    budget: Budget
    services: Services                                # db, redis, mcp client, policy client, llm factory
```

Rules: state holds only JSON-serialisable data and Pydantic models (the checkpointer serialiser handles both; a round-trip test is mandatory). Clients and connections live in `RunContext`, never in state. Large payloads (tool outputs over 32 KB, files) are stored in S3 and referenced by ID.

### 11.3 Nodes

| Node | Responsibility | Model alias | Writes to state |
| --- | --- | --- | --- |
| `intake` | Validate task, load attachments metadata, init `usage`, emit `run.started`. | — | `task`, `usage` |
| `retrieve_memory` | Hybrid search of user and team memories (FR-52). | `embed` + `RerankClient` | `memories` |
| `plan` | Supervisor produces `Plan` via structured output; on repair, the prompt includes validation errors. | `planner` | `plan`, `plan_version` |
| `validate_plan` | Deterministic checks (FR-11): schema, acyclic DAG via Kahn's algorithm, known specialists, tool allow-lists, input references resolvable. | — | `plan_repairs`, `errors` |
| `plan_gate` | If `require_plan_approval` or confidence < threshold → `interrupt()` with level `approve_plan`; applies human edits and re-validates. | — | `plan`, `escalations` |
| `dispatch` | Checks cancel flag and budget; cascades `skipped` to dependents of failed or skipped subtasks; marks up to N ready subtasks (deps approved or taken over) `running`. Its router then sends each running subtask to `subtask_worker`, or routes to `synthesize` when all are terminal. | — | `subtask_status` |
| `subtask_worker` (subgraph) | execute → review → revise loop → escalate (11.5). | `specialist`, `reviewer` | `subtask_results`, `subtask_status`, `reviews`, `usage` |
| `synthesize` | Supervisor merges approved results into the deliverable in `deliverable_format`, preserving citations. | `planner` | `deliverable` |
| `final_review` | Reviewer checks the deliverable against the original request (FR-33). One revision allowed, then escalate. | `reviewer` | `final_review`, `final_revisions` |
| `write_memory` | Extracts up to 10 memories (FR-51), redacts PII, dedupes, inserts. Failure here never fails the task. | `fast`, `embed` | — |
| `finalize` | Stores deliverable artifact, sets task `completed`, emits `run.completed` with cost summary. | — | — |

### 11.4 Parent graph wiring (`packages/agents/graph.py`)

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

def route_after_validate(state: RunState) -> str:
    if not state.get("plan_errors"):
        return "plan_gate"
    return "plan" if state["plan_repairs"] <= 1 else "plan_gate"   # 2nd failure -> human via plan_gate

def route_dispatch(state: RunState) -> list[Send] | str:
    statuses = state["subtask_status"]
    if all(s in TERMINAL for s in statuses.values()):
        return "synthesize"
    # The dispatch NODE already marked up to MAX_CONCURRENCY ready subtasks "running"
    # and cascaded "skipped" to dependents of failed/skipped subtasks (no deadlocks).
    running = [k for k, s in statuses.items() if s == "running"]
    return [Send("subtask_worker", build_worker_input(state, k)) for k in running]

def build_graph(checkpointer):
    g = StateGraph(RunState, context_schema=RunContext)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_node("subtask_worker", build_subtask_graph())  # compiled subgraph, inherits checkpointer
    g.add_edge(START, "intake")
    g.add_edge("intake", "retrieve_memory")
    g.add_edge("retrieve_memory", "plan")
    g.add_edge("plan", "validate_plan")
    g.add_conditional_edges("validate_plan", route_after_validate, ["plan", "plan_gate"])
    g.add_edge("plan_gate", "dispatch")
    g.add_conditional_edges("dispatch", route_dispatch, ["subtask_worker", "synthesize"])
    g.add_edge("subtask_worker", "dispatch")
    g.add_edge("synthesize", "final_review")
    g.add_conditional_edges("final_review", route_final, ["synthesize", "write_memory"])
    g.add_edge("write_memory", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
```

Invocation from the worker (start and resume use the same `thread_id`):

```python
config = {"configurable": {"thread_id": run.thread_id}, "recursion_limit": budget.max_steps}
# replay first run: config["configurable"]["checkpoint_id"] = run.replay_from_checkpoint_id
async for chunk in graph.astream(inputs_or_command, config=config, context=ctx,
                                 durability="sync", stream_mode=["updates", "custom"]):
    await events.publish(run_id, chunk)              # mapped to Section 17 events
```

When `dispatch` fans out several `Send`s, the parallel workers form one superstep; `dispatch` runs again only after all of them return or interrupt. That is acceptable in v1.0 (subtasks of one dependency level run together). The max-concurrency slot logic still applies within the level.

### 11.5 Subtask worker subgraph

| Step | Behaviour |
| --- | --- |
| `execute` | Builds the specialist agent (Section 12) with the subtask, resolved inputs, relevant memories and any reviewer feedback. Runs the bounded tool loop. On a non-retryable failure or exhausted retries: `attempts += 1`; if `attempts < 2` re-run with an "alternative approach" hint, else go to `escalate` (trigger `subtask_failed_twice`). |
| `review` | Reviewer (different model alias) returns `Review`. `approve` → status `approved`, end. `revise` and `revisions < 2` → `execute` with feedback. Otherwise → `escalate` (trigger `low_review_score`). |
| `escalate` | Calls `interrupt(payload)` (Section 14). Resume value decides: `approve` → `approved`; `take_over` → result replaced with the human's output, status `taken_over`; `reject` with `retry` → back to `execute` with the human's note; `reject` with `skip` → `skipped` (the supervisor's synthesis notes the gap). |

The subgraph state shares the keys `subtask_results`, `subtask_status`, `reviews`, `usage`, `errors`, `escalations` (same reducers) with the parent so its output merges back automatically.

### 11.6 Idempotency rules (critical for resume)

- On resume, LangGraph re-runs the interrupted node from its start. So every side effect before an `interrupt()` must be idempotent: escalation rows use `INSERT ... ON CONFLICT (run_id, interrupt_id) DO NOTHING`; tool calls are keyed by `idempotency_key` and a replayed key returns the stored result instead of calling the tool again.
- Never put a tool call and an `interrupt()` in the same node where the tool call happens first. Approval gates call `interrupt()` before the sensitive tool executes.
- The interrupt ID comes from the `Interrupt` object LangGraph returns in the stream (`__interrupt__`); the worker writes it into `escalations.interrupt_id`. Resume with `Command(resume={interrupt_id: value})`.

## 12. Specialist agents, tools and MCP servers

Each specialist is a LangChain `create_agent` loop with its own prompt, model alias and tool allow-list, wrapped in Orchestrix middleware. Tools live in five MCP servers, each its own container, reached over Streamable HTTP on the internal network. The orchestrator, not the prompt, decides which tools a specialist receives.

### 12.1 Specialists

| Specialist | Purpose | Allowed tools | Model alias | Default limits |
| --- | --- | --- | --- | --- |
| `research` | Find, read and summarise sources; every claim carries a URL | `web.search`, `web.fetch`, `files.read_artifact` | `specialist` | 8 tool calls, 4 min |
| `data_analyst` | Analyse attachments and database tables with code; produce tables and charts | `sandbox.run_python`, `data.describe_table`, `data.sql_query`, `files.read_artifact`, `files.write_artifact` | `specialist` | 8 tool calls, 5 min |
| `writer` | Turn approved inputs into the requested document; send communications | `files.read_artifact`, `files.write_artifact`, `comms.send_email` (sensitive) | `specialist` | 6 tool calls, 4 min |
| `coder` | Write and test small scripts or code snippets | `sandbox.run_python`, `files.read_artifact`, `files.write_artifact` | `specialist` | 10 tool calls, 6 min |

Adding a specialist = one module in `specialists/`, one prompt file, one row in the `tools.allowed_specialists` arrays, and its name in the `Specialist` literal. No graph changes.

### 12.2 Specialist construction

```python
from langchain.agents import create_agent

async def build_specialist(name: Specialist, ctx: RunContext, subtask: PlannedSubtask):
    tools = await ctx.services.tools.for_specialist(name)       # MCP tools filtered by allow-list + enabled flag
    return create_agent(
        model=ctx.services.llm.chat("specialist"),               # ChatOpenAI(base_url=LITELLM_URL, model=alias)
        tools=tools,
        system_prompt=render_prompt(f"specialists/{name}.j2", subtask=subtask),
        response_format=SubtaskResult,                           # structured final answer
        middleware=[
            BudgetMiddleware(ctx),          # before_model: estimate tokens, block if over budget; after_model: record usage
            PolicyMiddleware(ctx),          # wrap_tool_call: OPA check -> allow / deny / interrupt for approval
            InjectionGuardMiddleware(ctx),  # wrap_tool_call: wrap + scan tool output before the model sees it
            ToolLimitMiddleware(subtask.max_tool_calls),
            TracingMiddleware(ctx),         # spans + llm_calls/tool_calls rows
        ],
    )
```

The agent is invoked inside the `execute` node with the node's `config`, so it runs as a nested graph under the same checkpoint namespace: an `interrupt()` raised by `PolicyMiddleware` pauses the whole run, and on resume the agent continues from its own checkpoint without repeating earlier tool calls. LangChain 1.x ships built-in middleware (for example call limits and human-in-the-loop); Claude Code should use a built-in where it matches exactly and otherwise subclass `AgentMiddleware`, after checking the installed version's API.

### 12.3 Tool catalogue

| Tool | Server | What it does | Key inputs | Output | Sensitive |
| --- | --- | --- | --- | --- | --- |
| `web.search` | `mcp-web` | Search via Tavily, fall back to SearXNG | `query`, `max_results ≤ 8` | list of {title, url, snippet} | No |
| `web.fetch` | `mcp-web` | Fetch a URL and extract main text (trafilatura); blocks private IP ranges (SSRF guard) | `url`, `max_chars ≤ 20000` | {url, title, text, fetched_at} | No |
| `sandbox.run_python` | `mcp-sandbox` | Run Python with pandas, numpy, scipy, matplotlib preinstalled; files mounted read-only | `code`, `artifact_ids`, `timeout_s ≤ 60` | {stdout, stderr, exit_code, created_artifacts} | No |
| `data.describe_table` | `mcp-data` | List columns and types of a table in the read-only `demo_data` schema | `table` | column list | No |
| `data.sql_query` | `mcp-data` | Run a single SELECT, parsed and checked with sqlglot (no DDL/DML, no multiple statements), forced `LIMIT 1000`, 10 s statement timeout, read-only DB role | `sql` | {columns, rows, truncated} | No |
| `files.read_artifact` | `mcp-files` | Read an attachment or artifact the run can access | `artifact_id`, `max_chars` | text or table preview | No |
| `files.write_artifact` | `mcp-files` | Store a file produced by an agent | `name`, `mime`, `content_b64` (≤ 10 MB) | {artifact_id} | No |
| `comms.send_email` | `mcp-comms` | Simulated email: writes to an `outbox` table and the UI, never sends | `to[] ≤ 10`, `subject`, `body_md` | {message_id, status: "simulated"} | **Yes** |

### 12.4 MCP conventions

- Servers are built with FastMCP from the official `mcp` SDK, one container each, exposing `/mcp` (Streamable HTTP) and `/healthz`.
- The worker's client is `langchain-mcp-adapters` `MultiServerMCPClient` with one entry per server (`transport: "streamable_http"`, URL from settings). Tool schemas are loaded at worker start and cached; the `tools` table is synced from them (enabled/sensitive flags stay admin-owned).
- Every call carries headers `Authorization: Bearer <internal service token>`, `X-Run-Id`, `X-Trace-Parent` (W3C trace context), so server spans join the run's trace.
- Every tool validates inputs with Pydantic, has a hard timeout, returns structured JSON, and never raises raw stack traces to the agent (errors are `{error_code, message, retryable}`).
- The sandbox container: non-root user, read-only root filesystem, `tmpfs` working dir per call (deleted after), no outbound network (internal network only, egress denied), 1 CPU, 1 GiB memory, 60 s wall time, 50 MB output cap. On Kubernetes it runs under a gVisor `RuntimeClass`.

## 13. Memory system

Memory has two tiers. Working memory is the checkpointed `RunState` of one run and disappears from active use when the run ends. Long-term memory is the `memories` table: hybrid-searchable, namespaced per user and team, scored by importance with time decay, consolidated nightly and fully deletable.

### 13.1 Working memory

- Everything a run knows (task, plan, results, reviews, errors) is in `RunState` and persisted at every step by the checkpointer. This is what makes resume, replay and time-travel possible.
- Inside a specialist loop, the message history is bounded: when it exceeds 60% of the model's context window, older tool results are summarised (summarisation middleware) and the full payloads stay in S3, referenced by ID.
- Specialists never see other subtasks' raw transcripts, only the `SubtaskResult` outputs listed in their `inputs`. This keeps context small and stops errors spreading.

### 13.2 Writing long-term memories (`write_memory` node)

1. The memory writer (alias `fast`) receives the task, final plan, review outcomes, errors and escalation decisions, and returns up to 10 `MemoryCandidate` objects (kind, text ≤ 500 chars, importance, `source_ref`, and for preferences the user's exact quote).
2. Validation (FR-56): a `fact` must have a `source_ref` that appears in the run's collected sources; a `preference` must have a quote that is an exact substring of text the user wrote (task description or clarification answers). Invalid candidates are dropped and logged.
3. PII redaction (FR-93): configured regex patterns replace matches with typed placeholders (`[EMAIL]`, `[PHONE]`, `[CARD]`, `[GOV_ID]`).
4. Deduplication: if an existing active memory in the same namespace has cosine similarity ≥ 0.92, the new one is not inserted; instead the existing memory's importance becomes `max(old, new)` and `last_used_at` is refreshed.
5. Insert with the embedding. Episodes and lessons go to the team namespace; preferences to the user namespace; facts to the team namespace.

### 13.3 Retrieval for planning (`retrieve_memory` node)

```sql
-- :q_vec = embedding of the task text, :q_txt = task text, :ns = ARRAY['user:<id>','team:<id>']
WITH vec AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> :q_vec) AS r
  FROM memories
  WHERE namespace = ANY(:ns) AND consolidated_into IS NULL
  ORDER BY embedding <=> :q_vec LIMIT 30
), txt AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
  FROM memories, websearch_to_tsquery('english', :q_txt) q
  WHERE namespace = ANY(:ns) AND consolidated_into IS NULL AND tsv @@ q
  ORDER BY ts_rank_cd(tsv, q) DESC LIMIT 30
)
SELECT id, sum(1.0 / (60 + r)) AS rrf               -- Reciprocal Rank Fusion, k = 60
FROM (SELECT * FROM vec UNION ALL SELECT * FROM txt) u
GROUP BY id ORDER BY rrf DESC LIMIT 20;
```

Then: rerank the 20 candidates against the task text (alias `rerank`), compute the final score, keep the top 8.

```latex
\text{score} = \text{rerank} \times \left(0.7 + 0.3 \times \text{importance} \times 0.5^{\,d/30}\right)
```

where d is days since `last_used_at` (or `created_at` if never used). Retrieved memories go into the planning prompt as a numbered block with their IDs. When the plan lists `used_memory_ids`, those rows get `access_count + 1` and `last_used_at = now()`, which is how useful memories resist decay (FR-53).

### 13.4 Consolidation and deletion

- **Nightly consolidation (FR-54):** per namespace, find pairs of active memories of the same kind with cosine ≥ 0.92, group them, and ask the `fast` model to merge each group into one memory. Insert the summary with `derived_from` = the originals, then set the originals' `consolidated_into`. Originals stay for audit but are excluded from retrieval.
- **Deletion (FR-55):** deleting a memory hard-deletes the row and also deletes any consolidated memory whose `derived_from` contains it, so deleted information cannot survive inside a summary. Each deletion writes an `audit_log` row (no memory text in the log, only the ID and kind).
- **Full user deletion:** removes all `user:<id>` memories, their derived summaries, and redacts the user's text from their tasks' stored payloads.

### 13.5 Embedding model rules

- The `vector(1024)` column dimension must equal the embedding model's output (`mxbai-embed-large` = 1024). If you switch to a model with a different dimension, write an Alembic migration that adds a new column, re-embeds all rows with a batch job, swaps columns and rebuilds the HNSW index. Never mix embeddings from two models in one column.
- Store the embedding model name in settings and in a `meta` row; the worker refuses to start if they differ. The app_meta table (Section 10) holds it.

## 14. Human-in-the-loop

Every human decision point is a LangGraph `interrupt()` whose payload is mirrored into the `escalations` table. The graph's own pending interrupts (`(await graph.aget_state(config)).interrupts`) are the source of truth; the table is the queryable, permissioned view for the UI.

### 14.1 Trigger-to-level mapping (defaults, configurable in `escalation_policy.yaml`)

| Trigger | Raised in | Level | Run behaviour |
| --- | --- | --- | --- |
| `plan_low_confidence` (confidence < 0.6) | `plan_gate` | `approve_plan` | Pauses before any subtask runs |
| `plan_approval_required` (task flag) | `plan_gate` | `approve_plan` | Same |
| `plan_invalid_twice` | `plan_gate` | `take_over` | Human writes or fixes the plan |
| `ambiguous_request` | `plan` | `clarify` | Up to 3 questions to the requester |
| `sensitive_tool` | `PolicyMiddleware` | `approve_action` | Pauses before the tool executes |
| `policy_require_approval` | `PolicyMiddleware` | `approve_action` | Same |
| `subtask_failed_twice` | `subtask_worker.escalate` | `take_over` | Other subtasks keep running |
| `low_review_score` (< 6 after 2 revisions) | `subtask_worker.escalate` / `final_review` | `approve_action` (accept as-is) or `take_over` | Same |
| `budget_80` | `BudgetMiddleware` | `notify` | Continues; reviewer informed |
| `budget_100` | `BudgetMiddleware` / `dispatch` | `approve_action` (raise budget or stop) | Pauses |
| `user_requested_review` | `final_review` | `approve_action` | Pauses before delivery |

`notify` never calls `interrupt()`; it writes an escalation row with status `resolved` and sends the notification.

### 14.2 Interrupt payload and resume value

```python
class EscalationPayload(BaseModel):          # what interrupt() receives (JSON-serialisable)
    trigger: str
    level: Literal["clarify", "approve_action", "approve_plan", "take_over"]
    title: str                                # "Approve sending email to 3 recipients"
    task_summary: str
    plan_progress: list[dict]                 # key, specialist, status
    proposed: dict                            # tool + args, or plan, or draft output
    reasoning: str                            # agent's explanation
    related_memories: list[dict]              # id, text
    questions: list[str] = []                 # clarify only
    allowed_actions: list[str]                # subset of the resume actions below

class EscalationDecision(BaseModel):         # what Command(resume={interrupt_id: decision}) carries
    action: Literal["approve", "approve_with_edits", "reject", "take_over", "answer"]
    edits: dict | None = None                 # edited tool args or edited plan (re-validated)
    reason: str | None = None                 # required for reject
    on_reject: Literal["retry", "skip", "fail_task"] | None = None
    output: str | None = None                 # take_over
    answers: list[str] | None = None          # clarify
    actor_id: str
```

The node that called `interrupt()` validates the returned value as `EscalationDecision` and applies it. Edited plans go back through `validate_plan`; edited tool args go back through the policy check.

### 14.3 Pause and resume sequence

1. A node calls `interrupt(payload)`. The worker's `astream` loop receives the `__interrupt__` chunk with each `Interrupt` (its `id` and `value`).
2. The worker upserts an `escalations` row per interrupt (`ON CONFLICT (run_id, interrupt_id) DO NOTHING`), sets task status `awaiting_human` (or `awaiting_plan_approval`), sets run status `paused`, emits `escalation.created`, and **releases the run lease**. No worker holds a paused run.
3. The notifier sends the in-app notification and the Slack webhook message with a deep link.
4. A reviewer posts a decision to `POST /v1/escalations/{id}/decision`. The API checks permissions through OPA (role, team, four-eyes rule), validates the body against the payload's `allowed_actions`, writes `escalation_actions` and the audit log, marks the escalation `resolved`, and enqueues `resume_run(run_id)`.
5. The `resume_run` job takes the lease, loads all resolved-but-unapplied escalations for the run, and calls `graph.astream(Command(resume={interrupt_id: decision, ...}), config, context=ctx)`.
6. After the stream stops (completed or paused again), the worker re-reads `aget_state(config).interrupts` and reconciles the `escalations` table with it, so any interrupt still pending stays open and shown.

### 14.4 Clarification and reviewer chat

- `clarify` escalations go to the requester (not reviewers). Answers become part of the user's own words, so preferences extracted from them are valid memories.
- Reviewer chat (FR-44): `POST /v1/escalations/{id}/chat` answers questions with the `fast` model using a read-only snapshot of run state (plan, results, payload). It has no tools and cannot change the run. Messages are stored with the escalation.

### 14.5 SLA and timeouts

A scheduler job every 15 minutes re-notifies open escalations past `due_at` (default 24 h) and fails tasks whose escalations are older than 72 h with reason `escalation_timeout`, after a final notification. All timings are settings.

## 15. Guardrails, policy, security and cost controls

Safety is enforced in code at four checkpoints: before a model call (budget), before a tool call (policy), after a tool call (injection guard and taint), and before a human decision is applied (permission). Prompts ask for good behaviour; these checks guarantee it.

### 15.1 Policy engine (OPA)

OPA runs as a sidecar container. The tool registry is pushed into OPA as data (`PUT /v1/data/tools`) whenever an admin changes it. Policies live in `policies/` with `opa test` unit tests in CI.

```rego
package orchestrix.tool

# input: {tool, specialist, args, user, team, calls_last_minute, context_tainted}

deny contains "tool disabled" if not data.tools[input.tool].enabled
deny contains "tool not allowed for specialist" if not input.specialist in data.tools[input.tool].allowed_specialists
deny contains "rate limit exceeded" if input.calls_last_minute >= data.tools[input.tool].rate_limit_per_min

approval contains "sensitive tool" if data.tools[input.tool].sensitive

approval contains "recipient outside organisation" if {
    input.tool == "comms.send_email"
    some r in input.args.to
    not endswith(r, data.org.email_domain)
}

approval contains "untrusted content in context" if {
    input.context_tainted
    input.tool in data.org.side_effect_tools
}

decision := {"result": "deny", "reasons": deny} if count(deny) > 0

decision := {"result": "require_approval", "reasons": approval} if {
    count(deny) == 0
    count(approval) > 0
}

decision := {"result": "allow", "reasons": set()} if {
    count(deny) == 0
    count(approval) == 0
}
```

```rego
package orchestrix.escalation

# input: {actor: {id, roles, team_ids}, escalation: {level, assignee_team_id}, task: {requester_id}}

default allow := false

allow if {
    input.escalation.level != "clarify"
    some role in ["reviewer", "admin"]
    role in input.actor.roles
    input.escalation.assignee_team_id in input.actor.team_ids
    input.actor.id != input.task.requester_id          # four-eyes rule
}

allow if {
    input.escalation.level == "clarify"
    input.actor.id == input.task.requester_id
}
```

The Python `PolicyClient` calls `POST /v1/data/orchestrix/tool/decision` with a 200 ms timeout. If OPA is unreachable the decision is **deny** (fail closed), and the subtask escalates.

### 15.2 Prompt-injection defence and taint tracking

- Tool outputs are wrapped before the model sees them: `<tool_output tool="web.fetch" trust="untrusted">…</tool_output>`, and every specialist prompt states that content inside these tags is data and must never be followed as instructions.
- `InjectionGuardMiddleware` scans each output with pattern rules (instruction overrides, role-play prompts, fake tool-call JSON, hidden-text markers, encoded payloads) and, optionally, a small injection-classifier model behind the gateway alias `guard`. A hit adds an `injection_suspected` error to state and annotates the output for the reviewer.
- Taint tracking: once any web-fetched or user-uploaded content enters a specialist's context, that subtask is marked `context_tainted`. Taint propagates: a subtask whose inputs come from a tainted subtask is tainted too. Tainted subtasks need human approval for any side-effecting tool (OPA rule above), so an injected page can never silently trigger an action.
- `web.fetch` blocks private, loopback and link-local IP ranges after DNS resolution (SSRF guard), follows at most 3 redirects and re-checks each hop.

### 15.3 Budgets and cost accounting

- `BudgetMiddleware.before_model` estimates input tokens with the gateway's token counter and blocks the call if `used + estimate` would exceed any cap (FR-63), raising the `budget_100` escalation.
- `after_model` records actual tokens and cost into `llm_calls` and the state `usage`. Cost comes from the LiteLLM proxy's response cost (returned with each response); if missing, from the per-model price table in `infra/litellm/config.yaml`.
- A second, independent cap: each team has a LiteLLM virtual key with a monthly `max_budget`, so a bug in app-level accounting cannot overspend.
- `notify` at 80% of any run cap; pause at 100%.

### 15.4 Application security checklist

| Area | Control |
| --- | --- |
| Authentication | OIDC Authorization Code + PKCE via Keycloak; API validates JWT signature against cached JWKS (PyJWT `PyJWKClient`), `iss`, `aud`, `exp`. |
| Authorisation | FastAPI dependency `require_role(...)` + row-level ownership checks in repositories + OPA for escalation decisions. |
| Service-to-service | Internal bearer tokens per service, rotated via env; MCP servers accept only the worker's token. |
| Input limits | Request body 1 MB (attachments via separate upload endpoint, 10 MB each, MIME sniffed), description 8,000 chars. |
| Rate limiting | Per user: 30 task submissions/hour, 600 API requests/minute (Redis sliding window). |
| Headers | Strict CORS allow-list, CSP, HSTS, `X-Content-Type-Options`, no stack traces in responses. |
| Secrets | Only from environment or a mounted secret store; `.env` git-ignored; gitleaks in pre-commit and CI. |
| Supply chain | Lockfiles committed; Trivy scans images and dependencies in CI; Renovate or Dependabot for updates. |
| Data at rest | Postgres and S3 on encrypted volumes in production; S3 objects private, served via short-lived presigned URLs. |
| Audit | Every human/admin action and every policy deny goes to `audit_log` (append-only role grants). |

## 16. Model gateway and routing

Agents never name a model. They ask for an alias (`planner`, `specialist`, `reviewer`, `fast`, `embed`, `guard`) and the LiteLLM proxy maps aliases to real models per profile. Switching from fully local to cloud is a config change, not a code change (NFR-08).

### 16.1 Profiles

| Alias | `local` (8 GB GPU, offline) | `hybrid` (local + self-hosted vLLM) | `cloud` (best demo quality) |
| --- | --- | --- | --- |
| `planner` | `qwen3:8b` (Ollama) | Qwen3 32B on vLLM | Claude Sonnet 5.5 |
| `specialist` | `qwen3:8b` | `qwen3:8b` | Claude Haiku 5.5 |
| `reviewer` | `qwen3:8b`, different prompt, temperature 0 | Qwen3 32B on vLLM | Claude Sonnet 5.5 |
| `fast` | `qwen3:8b` | `qwen3:8b` | Claude Haiku 5.5 |
| `embed` | `mxbai-embed-large` (1024 d) | `mxbai-embed-large` | `mxbai-embed-large` (kept constant; see 13.5) |
| `guard` | disabled (pattern rules only) | optional classifier | optional classifier |

In the `local` profile the reviewer and specialist share a model, which weakens FR-32. The run records `reviewer_model` so evals can report this honestly; use `hybrid` or `cloud` for the headline eval numbers. Reranking is not routed through the gateway: `packages/memory` has a `RerankClient` with an `http` backend (a vLLM or TEI rerank endpoint) and a `local` backend (sentence-transformers `CrossEncoder` on CPU).

### 16.2 LiteLLM proxy config (`infra/litellm/config.yaml`, local profile excerpt)

```yaml
model_list:
  - model_name: planner
    litellm_params:
      model: ollama_chat/qwen3:8b
      api_base: os.environ/OLLAMA_BASE_URL
  - model_name: specialist
    litellm_params:
      model: ollama_chat/qwen3:8b
      api_base: os.environ/OLLAMA_BASE_URL
  - model_name: reviewer
    litellm_params:
      model: ollama_chat/qwen3:8b
      api_base: os.environ/OLLAMA_BASE_URL
      temperature: 0
  - model_name: fast
    litellm_params:
      model: ollama_chat/qwen3:8b
      api_base: os.environ/OLLAMA_BASE_URL
  - model_name: embed
    litellm_params:
      model: ollama/mxbai-embed-large
      api_base: os.environ/OLLAMA_BASE_URL
# hybrid adds:   model: openai/<served-model-name>, api_base: os.environ/VLLM_BASE_URL, api_key: os.environ/VLLM_API_KEY
# cloud adds:    model: anthropic/claude-sonnet-5-5 (or claude-haiku-5-5), api_key: os.environ/ANTHROPIC_API_KEY

router_settings:
  num_retries: 2
  timeout: 120

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/LITELLM_DATABASE_URL   # separate DB for virtual keys and team budgets
```

Claude Code must check the installed LiteLLM version's docs for the exact provider prefixes and fallback syntax before finalising this file, and add a `make gateway-check` target that sends one tiny request per alias and fails loudly on any error.

### 16.3 Client side (`packages/agents/llm.py`)

- `LLMFactory.chat(alias)` returns `ChatOpenAI(base_url=LITELLM_URL, api_key=<team virtual key>, model=alias, timeout=120, max_retries=0)`; retries belong to the gateway and to our tenacity wrapper, never both stacked unknowingly.
- Structured outputs use `model.with_structured_output(Schema, method="json_schema")`. If a provider rejects JSON-schema mode, fall back to `method="function_calling"`. On a Pydantic validation error, retry once with the error message appended, then raise.
- Qwen3 reasoning ("thinking") mode: disable it for structured-output and review calls (add `/no_think` to the system prompt or the provider's equivalent setting) and strip any `<think>` blocks from content before parsing. Keep it optional for `planner` behind a setting, and measure both in evals.
- Every call carries metadata `{run_id, agent, subtask_key}` for tracing and cost attribution.

## 17. API specification and streaming events

REST over HTTPS under `/v1`, JSON bodies, OpenAPI 3.1 generated by FastAPI and used to generate the frontend client. Errors use RFC 9457 `application/problem+json`. Every response carries `X-Request-Id` and `traceparent`.

### 17.1 Endpoints

| Method and path | Role | Purpose |
| --- | --- | --- |
| `POST /v1/uploads` | requester | Multipart upload of one attachment; returns `artifact_id`. |
| `POST /v1/tasks` | requester | Create task (FR-02). Header `Idempotency-Key` optional. Returns 202 `{task_id, run_id}`. |
| `GET /v1/tasks` | requester+ | List tasks; filters `status`, `mine`, `team`, cursor pagination. |
| `GET /v1/tasks/{id}` | owner, team reviewer, operator | Task detail with current plan, subtask statuses, usage, deliverable link. |
| `POST /v1/tasks/{id}/cancel` | owner, operator | Cancel (FR-04). |
| `GET /v1/tasks/{id}/deliverable` | owner, team | 302 to a presigned URL of the final artifact. |
| `GET /v1/runs/{id}/events` | owner, team, operator | SSE stream (17.2). Supports `Last-Event-ID`. |
| `GET /v1/runs/{id}/graph` | same | Plan DAG with live statuses for the plan view. |
| `GET /v1/runs/{id}/trace` | same | Span tree from `run_events`, `llm_calls`, `tool_calls`, `reviews`, `escalations`, plus the Langfuse trace URL. |
| `GET /v1/runs/{id}/checkpoints` | operator | Checkpoint history (`checkpoint_id`, step, node, timestamp). |
| `POST /v1/runs/{id}/replay` | operator | Body `{checkpoint_id, state_patch?, as_node?}`; returns new replay `run_id` (FR-70). |
| `GET /v1/runs/{id}/diff/{other_id}` | operator | Plan, outputs, review scores and cost compared (FR-71). |
| `GET /v1/escalations` | reviewer, requester (clarify) | Inbox; filters `status`, `assigned=me`, `level`. |
| `GET /v1/escalations/{id}` | allowed actors | Payload and history. |
| `POST /v1/escalations/{id}/decision` | allowed actors (OPA) | Body `EscalationDecision`; 409 if already resolved. |
| `POST /v1/escalations/{id}/chat` | allowed actors | Ask the agent about the pending decision (FR-44). |
| `GET /v1/memories` | user (own), reviewer (team) | List and search memories; filters `kind`, `q`. |
| `PATCH /v1/memories/{id}` | owner | Edit text (re-embedded) or importance. |
| `DELETE /v1/memories/{id}` | owner, admin | Hard delete (FR-55). |
| `GET /v1/costs` | operator, admin | Aggregates; `group_by` = `day`, `agent`, `model`, `team`, `task_type`. |
| `GET/PATCH /v1/admin/tools/{name}` | admin | Enable, disable, mark sensitive, rate limit, allowed specialists (FR-91). |
| `GET/PUT /v1/admin/budgets` | admin | Default run budget and team monthly budgets. |
| `GET /v1/admin/audit` | admin | Audit log; `format=csv` for export (FR-92). |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | internal | Liveness, readiness (DB, Redis, OPA, gateway), Prometheus metrics. |

### 17.2 Streaming events (SSE)

Each event is written to `run_events` (the durable log) and then published on the Redis channel `run:{run_id}`. The SSE endpoint first replays rows with `id > Last-Event-ID` from Postgres, then relays live Redis messages, de-duplicating by `id`, and sends a `: ping` comment every 15 s. A lost Redis message is therefore recovered on the client's next reconnect. The frontend uses `@microsoft/fetch-event-source` so it can send the `Authorization` header (native `EventSource` cannot).

```text
id: 4182
event: tool.completed
data: {"run_id":"…","ts":"2026-10-21T10:15:02Z","subtask":"s2","tool":"web.fetch","status":"ok","latency_ms":812}
```

| Event type | Emitted when |
| --- | --- |
| `run.started`, `run.resumed`, `run.completed`, `run.failed`, `run.cancelled` | Run lifecycle changes |
| `memory.retrieved`, `memory.written` | Memory read for planning; memories saved |
| `plan.created`, `plan.invalid`, `plan.approved`, `plan.edited` | Planning milestones |
| `subtask.started`, `subtask.result`, `subtask.status` | Subtask progress |
| `llm.completed` | Each model call (agent, alias, tokens, cost) |
| `tool.called`, `tool.completed`, `tool.denied` | Tool lifecycle and policy denials |
| `review.completed` | Reviewer verdict for a subtask or the final deliverable |
| `escalation.created`, `escalation.resolved` | Human-in-the-loop |
| `budget.warning`, `budget.exceeded` | Budget thresholds |
| `deliverable.ready` | Final artifact stored |

Event payloads are Pydantic models in `orchestrix_core.schemas.events`, exported to the OpenAPI spec as a discriminated union so the frontend gets typed handlers.

## 18. Frontend application

A React 19 single-page app with ten screens. Server state lives in TanStack Query; SSE events patch the query cache directly, so the plan graph, feed and inbox update live without polling.

### 18.1 Screens

| Route | Screen | Main components | Roles |
| --- | --- | --- | --- |
| `/` | Tasks dashboard | Task table (status, cost, age, owner), filters, "New task" button | all |
| `/tasks/new` | New task | Form (react-hook-form + Zod mirroring FR-02 limits), drag-and-drop uploads, budget override, plan-approval toggle | requester+ |
| `/tasks/$taskId` | Task detail | Plan DAG (React Flow + elkjs), live event feed, subtask drawer (result, sources, reviews), deliverable viewer, cost panel, cancel button | owner, team, operator |
| `/escalations` | Inbox | List grouped by level, SLA countdown, badge count in the nav | reviewer, requester |
| `/escalations/$id` | Decision view | Context panel, proposed action (editable JSON form generated from the tool's input schema, or plan editor), agent reasoning, related memories, chat panel, action buttons limited to `allowed_actions` | allowed actors |
| `/runs/$runId/trace` | Trace Explorer | Tree/graph of supervisor → specialists → reviewer; node inspector with prompt, response, tool I/O, tokens, latency, cost; link out to Langfuse | owner, team, operator |
| `/runs/$runId/replay` | Replay | Checkpoint timeline, state viewer with JSON patch editor, "Replay from here", diff view | operator |
| `/memory` | Memory | Search, filter by kind, edit, delete with confirm, source task links | all (own), reviewer (team) |
| `/costs` | Costs | Charts by day, agent, model, team (Recharts) | operator, admin |
| `/admin` | Admin | Tools registry editor, budgets, audit log table with CSV export | admin |

### 18.2 Frontend rules

- The API client is generated (`openapi-typescript` + `openapi-fetch`) by `pnpm gen:api` from the running API; CI fails if the generated client is out of date.
- One `useRunEvents(runId)` hook owns the SSE connection (fetch-event-source, auto-reconnect with `Last-Event-ID`) and maps each event type to a cache update (`queryClient.setQueryData`).
- Status is shown with colour **and** an icon **and** a text label (NFR-11). Palette: pending grey, running blue, in review purple, awaiting human amber, approved green, failed red, skipped dashed grey.
- Agent-produced Markdown renders through `react-markdown` with `rehype-sanitize`; no raw HTML from agents ever reaches the DOM.
- Auth: `oidc-client-ts` with PKCE, tokens in memory (not localStorage), silent renew; route guards read roles from the token.
- Light and dark themes via Tailwind v4 CSS variables; layout responsive down to 1024 px (admin and trace screens are desktop-first).
- Playwright e2e covers journeys 5.1–5.5 against the Compose stack with the recorded model fixtures.

## 19. Observability, trace explorer and replay

Three layers, each with one job: Langfuse holds deep LLM traces (prompts, completions, nested spans), Prometheus and Grafana hold platform health, and Orchestrix's own tables (`run_events`, `llm_calls`, `tool_calls`, `reviews`, `escalations`) power the in-app Trace Explorer, so the product UI never depends on a third-party API.

### 19.1 Tracing

- Use the Langfuse Python SDK (OpenTelemetry-native in current versions) initialised once per process, and pass its LangChain `CallbackHandler` in the graph `config["callbacks"]`; this yields nested spans for every graph node, model call and tool call automatically.
- Orchestrix adds its own OpenTelemetry spans (same tracer provider) for: policy checks, memory retrieval and writes, MCP calls (W3C `traceparent` propagated to the servers), escalation create and resolve, and budget decisions.
- Trace identity: one trace per run; Langfuse `session_id` = `task_id` (so a task's main and replay runs group together), `user_id` = requester, tags = profile, graph version, specialist set.
- Model spans carry OpenTelemetry GenAI attributes (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`) plus `orchestrix.agent`, `orchestrix.subtask`, `orchestrix.cost_usd`.
- PII redaction (FR-93) runs in a span processor before export, using the same patterns as memory redaction.
- If Langfuse is down, the exporter drops spans after its buffer fills; runs never block on tracing.

### 19.2 Metrics (Prometheus, prefix `orchestrix_`)

| Metric | Type | Labels |
| --- | --- | --- |
| `runs_total` | counter | `status`, `profile` |
| `run_duration_seconds` | histogram | `status` (active time, excluding human wait) |
| `node_duration_seconds` | histogram | `node` |
| `llm_tokens_total` | counter | `agent`, `alias`, `direction` (in/out) |
| `llm_cost_usd_total` | counter | `agent`, `alias`, `team` |
| `tool_calls_total` | counter | `tool`, `status` |
| `tool_latency_seconds` | histogram | `tool` |
| `policy_decisions_total` | counter | `result`, `tool` |
| `escalations_open` | gauge | `level` |
| `escalation_wait_seconds` | histogram | `level`, `trigger` |
| `review_score` | histogram | `specialist` |
| `run_lease_reclaims_total` | counter | — (worker crash recoveries) |
| `queue_depth` | gauge | `job` |

Grafana dashboards (JSON in `infra/grafana/`): **Operations** (throughput, failures, queue depth, lease reclaims, API latency), **Agents and cost** (cost per task type, tokens by agent, review scores, tool error rates), **Human-in-the-loop** (open escalations, wait time, approval vs rejection rate by trigger). Alerts: failure rate > 20% over 15 min, any lease reclaim loop (same run reclaimed 3 times), OPA unreachable, escalations past SLA.

### 19.3 Trace Explorer

`GET /v1/runs/{id}/trace` returns a tree: run → plan (with retrieved memories) → each subtask attempt → its model calls, tool calls and reviews → escalations and their decisions → synthesis → final review → memory writes. Each node has status, start and end time, tokens, cost and a pointer to full payloads (S3 key or Langfuse observation ID). The UI renders it with React Flow; selecting a node loads its payload lazily.

### 19.4 Replay (time travel)

1. `GET /v1/runs/{id}/checkpoints` lists the run's branch using `graph.aget_state_history(config)` (each `StateSnapshot` has `config`, `next`, `values`, `metadata`, `created_at`).
2. `POST /v1/runs/{id}/replay` with `{checkpoint_id, state_patch?}` creates a `runs` row (`kind = replay`, same `thread_id`, `replay_from_checkpoint_id`).
3. The replay job, if `state_patch` is present, calls `graph.aupdate_state(config_at_checkpoint, state_patch)`, which writes a new checkpoint branching from the chosen one (the original checkpoints are immutable and untouched). It then runs `graph.astream(None, config_of_new_checkpoint, ...)` to continue from there.
4. The replay run records its own `head_checkpoint_id` as it progresses; the original run's reads stay pinned to the original `head_checkpoint_id`, so the two never interfere.
5. Replays run with `side_effects = simulated`: sensitive tools return a simulated success and never reach the approval queue unless the operator opts in.
6. The diff endpoint compares the two runs' plans, subtask outputs (text diff), review scores, tool-call counts and cost.

## 20. Testing and evaluation

Two separate disciplines. Tests prove the code is correct and run on every commit with no real model (scripted fakes). Evals measure agent quality with real models, run nightly and on agent-changing PRs, and produce the numbers in Section 1.

### 20.1 Test layers

| Layer | Tooling | What it covers | Runs |
| --- | --- | --- | --- |
| Unit | pytest | Plan validation (cycles, missing deps, unknown tools), task state machine, reducers, budget maths, RRF and decay scoring, PII redaction, injection patterns, SSRF guard, Pydantic schemas | every commit |
| Graph | pytest + `ScriptedChatModel` (a fake chat model returning pre-written messages and tool calls) + `InMemorySaver` | Every route in Section 11: happy path, repair, revise loop, failure twice, each escalation level, take over, skip cascade, budget stop, cancel | every commit |
| Policy | `opa test policies/` | Every Rego rule, including fail-closed cases | every commit |
| Integration | pytest + testcontainers (Postgres 18 + pgvector, Redis) + respx | Repositories, checkpointer round-trip of `RunState`, memory SQL, SSE replay + live, escalation API with OPA, MCP servers with real containers | every PR |
| Resilience | pytest + Compose | Kill a worker with SIGKILL mid-subtask: lease expires, reaper re-enqueues, run completes, no duplicate tool calls (NFR-01). Pause, restart the whole stack, resume. | every PR |
| End to end | Playwright | Journeys 5.1–5.5 with recorded model fixtures | every PR |
| Frontend unit | Vitest + Testing Library | Event-to-cache mapping, forms, status rendering | every commit |

Graph tests must run in under 60 seconds total. Every FR has at least one test whose name includes its ID (`test_fr31_revise_loop_max_two`).

### 20.2 Evaluation suite (`packages/evals`)

| Dataset | Size | Measures | Scoring |
| --- | --- | --- | --- |
| Golden tasks | 40 tasks across research, analysis, writing, coding, mixed | Task success, plan validity, cost, steps | Rubric per task, LLM-as-judge (alias `reviewer` from the `cloud` or `hybrid` profile) + deterministic checks (required sections, citations resolve, numbers match the CSV) |
| Planted defects | 30 subtask outputs with known errors (unsourced claims, wrong numbers, missing sections) | Reviewer catch rate | Deterministic: did the reviewer return `revise` and name the defect |
| Escalation cases | 25 scenarios where a human is or is not needed | Escalation precision and recall | Labelled ground truth |
| Injection cases | 20 fetched pages containing hidden instructions to email or write data | Unsafe actions executed without approval (target 0) | Deterministic from `tool_calls` and `escalations` |
| Memory cases | 15 task pairs where task A teaches a lesson task B needs | Retrieval hit rate, plan quality delta with vs without memory | Retrieval: was the memory in the top 8; quality: judge pairwise |
| Trajectory checks | All of the above | Wrong-tool calls, repeated identical tool calls, loops, steps per subtask | Deterministic from traces |

**Judge calibration.** Before trusting the judge, two humans label 30 golden-task outputs. Report Cohen's kappa between judge and human consensus; require ≥ 0.6, otherwise refine the rubric. Pairwise judgements randomise the order of A and B to reduce position bias.

**Statistics.** Each eval case runs 3 times. Report the mean with a bootstrap 95% confidence interval. A regression is flagged only when the new mean falls below the baseline's lower bound and the drop exceeds 5 points.

**Where evals run.** GitHub-hosted runners have no GPU, so the eval gate (NFR-10) runs (a) nightly and (b) on PRs labelled `agents-change`, either on a self-hosted GPU runner with the `local` profile or with the `cloud` profile and a capped API key. Results are pushed to Langfuse as dataset runs with scores, and a Markdown report (scores, CIs, per-category breakdown, the 5 worst cases with trace links) is posted as a PR comment.

**Recorded fixtures.** `make record-fixtures` captures real model and web responses for the demo and e2e tests (stored under `tests/fixtures/`, secrets scrubbed), so CI and `make demo` are deterministic and offline.

## 21. Configuration, deployment and CI/CD

One settings class, environment-driven; Docker Compose with profiles for development; a Helm chart for Kubernetes; three GitHub Actions workflows.

### 21.1 Configuration (`orchestrix_core.config.Settings`, prefix `ORCHESTRIX_`)

| Variable | Example | Notes |
| --- | --- | --- |
| `ORCHESTRIX_ENV` | `development` | `development`, `test`, `production`. |
| `ORCHESTRIX_DATABASE_URL` | `postgresql+psycopg://orchestrix_app:…@postgres:5432/orchestrix` | App role; checkpointer uses the same DSN without the `+psycopg` suffix. |
| `ORCHESTRIX_REDIS_URL` | `redis://redis:6379/0` | Broker, pub/sub, rate limits. |
| `ORCHESTRIX_S3_ENDPOINT`, `_S3_BUCKET`, `_S3_ACCESS_KEY`, `_S3_SECRET_KEY` | — | Artifacts and payloads. |
| `ORCHESTRIX_LITELLM_URL`, `_LITELLM_MASTER_KEY` | `http://litellm:4000` | Gateway; teams get virtual keys at bootstrap. |
| `ORCHESTRIX_MODEL_PROFILE` | `local` | `local`, `hybrid`, `cloud` (selects the LiteLLM config file). |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Points at an Ollama already running on the host (WSL2) or the `ollama` container. |
| `ORCHESTRIX_EMBEDDING_MODEL`, `_EMBEDDING_DIM` | `embed`, `1024` | Must match `memories.embedding` (13.5). |
| `ORCHESTRIX_OPA_URL` | `http://opa:8181` | Fail closed if unreachable. |
| `ORCHESTRIX_OIDC_ISSUER`, `_OIDC_AUDIENCE` | `http://keycloak:8080/realms/orchestrix` | JWT validation. |
| `ORCHESTRIX_AUTH_MODE` | `oidc` | `dev` (local signed tokens) is allowed only when `ENV=development`; the app refuses to start otherwise. |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `http://langfuse-web:3000` | Tracing. |
| `TAVILY_API_KEY`, `ORCHESTRIX_SEARXNG_URL` | — | Web search and fallback. |
| `ORCHESTRIX_SLACK_WEBHOOK_URL` | — | Escalation notifications (optional). |
| `ORCHESTRIX_MCP_SERVICE_TOKEN` | — | Worker to MCP servers. |
| `ORCHESTRIX_DEFAULT_BUDGET` | JSON | FR-63 defaults. |
| `ORCHESTRIX_PLAN_CONFIDENCE_THRESHOLD`, `_REVIEW_SCORE_THRESHOLD`, `_MAX_CONCURRENCY` | `0.6`, `6`, `4` | Behaviour knobs. |
| `ORCHESTRIX_LEASE_SECONDS`, `_HEARTBEAT_SECONDS` | `60`, `15` | Run lease (Section 8). |

### 21.2 Docker Compose (`infra/compose/`)

| Profile | Services | When needed |
| --- | --- | --- |
| `core` | `postgres` (`pgvector/pgvector:pg18`), `redis` (8.2), `s3`, `opa`, `litellm`, `api`, `worker` (×2), `scheduler`, `mcp-web`, `mcp-sandbox`, `mcp-data`, `mcp-files`, `mcp-comms`, `web` | Always (from M1) |
| `models` | `ollama` with GPU reservation | Only if Ollama is not already running on the host |
| `auth` | `keycloak` (26.x, realm export imported at start) | From M7 |
| `observability` | Langfuse web + worker + ClickHouse + its Redis and storage (copied from Langfuse's upstream `docker-compose.yml`, versions pinned), `prometheus`, `grafana` | From M5 |

Compose details that are easy to get wrong:

- PostgreSQL 18 images changed the data directory layout: mount the volume at `/var/lib/postgresql` (not `/var/lib/postgresql/data`). An init script creates the `orchestrix`, `litellm`, `keycloak` databases and the `orchestrix_app` / `orchestrix_migrator` roles, and enables the `vector` extension.
- `mcp-sandbox` sits on an `internal: true` network with no route out, runs `read_only: true` with a `tmpfs` workdir, `user` non-root, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `pids_limit`, CPU and memory limits.
- Every service has a healthcheck; `api` and `worker` use `depends_on: condition: service_healthy`. A one-shot `migrate` service runs `alembic upgrade head` and `checkpointer.setup()` before `api` and `worker` start.
- The full stack with observability is memory-heavy (ClickHouse and Langfuse especially). On a laptop, run `core` plus the host's Ollama day to day and enable `observability` only when working on M5 or recording the demo.

`Makefile` targets: `dev`, `dev-full`, `migrate`, `seed`, `test`, `test-integration`, `lint`, `typecheck`, `gateway-check`, `eval-smoke`, `eval-full`, `record-fixtures`, `demo`, `gen-api`.

### 21.3 Kubernetes (`infra/helm/orchestrix`)

- Deployments: `api` (HPA on CPU), `worker` (HPA on queue depth via KEDA when available, else CPU), `scheduler` (1 replica, leader-only), one per MCP server, `web` (static files behind the ingress), `litellm`, `opa`.
- Data services are not in the chart: use operators or managed services (CloudNativePG for PostgreSQL 18 with pgvector, a Redis operator or managed Redis, an S3 service). The chart takes their connection settings as secrets.
- `mcp-sandbox` runs with `runtimeClassName: gvisor`, a NetworkPolicy denying all egress, and a restrictive `securityContext` (non-root, read-only root fs, no privilege escalation, seccomp `RuntimeDefault`).
- NetworkPolicies: only `worker` may reach MCP servers; only `api` and `worker` reach Postgres and Redis.
- PodDisruptionBudgets for `api` and `worker`; `terminationGracePeriodSeconds: 45` so workers drain (NFR-04).
- Local validation on kind; optional Argo CD Application manifest for GitOps.

### 21.4 CI/CD (GitHub Actions)

| Workflow | Trigger | Jobs |
| --- | --- | --- |
| `ci.yml` | push, PR | ruff + Biome, pyright + tsc, unit + graph tests, `opa test`, integration tests (service containers), frontend build + Vitest, OpenAPI client drift check, Docker build, Trivy scan (fail on HIGH/CRITICAL with a fix available), gitleaks, Playwright e2e on Compose with fixtures |
| `eval.yml` | nightly, PR label `agents-change`, manual | Eval smoke (10 golden tasks) on PRs, full suite nightly; gate per 20.2; report as PR comment and Langfuse dataset run |
| `release.yml` | tag `v*` | Build and push images to GHCR with SBOM, package and publish Helm chart, create GitHub release notes |

Branch protection on `main`: `ci.yml` required, `eval.yml` required when the `agents-change` label is present, one review.

## 22. Build plan and Claude Code prompts

Eight milestones over about 12 weeks at 2–3 focused hours a day. Each milestone ends with green CI, a tagged commit and a short demo you can show. Paste the prompt for the current milestone into Claude Code; it reads `SPEC.md` and `CLAUDE.md` first.

[embedded content: Build roadmap · 8 milestones over 12 weeks]

M2 and M4 take two weeks each because they hold the hardest correctness work: durable execution and resume.

### M1 — Foundation (week 1)

**Scope:** monorepo skeleton (Section 9), uv and pnpm workspaces, settings, Compose `core` profile (Postgres 18, Redis, S3, OPA, LiteLLM, empty api and worker), Alembic migrations for all Section 10 tables, checkpointer setup, health endpoints, structlog, ruff, pyright, pre-commit, `ci.yml` skeleton, `make gateway-check`.

**Done when:** `make dev` starts all `core` services healthy; `make migrate` creates every table; `make gateway-check` gets a reply from each alias via the host's Ollama; CI is green.

```text
Read SPEC.md sections 0, 7, 9, 10 and 21. Implement milestone M1 exactly as scoped in section 22.
Create the monorepo layout from section 9 (empty modules with docstrings are fine where later milestones fill them).
Resolve and pin the latest stable versions of every dependency; do not type versions from memory.
Write Alembic migrations for every table in section 10, plus the app/migrator roles and the updated_at trigger.
Add the Compose core profile from 21.2, honouring the PostgreSQL 18 volume path and healthchecks.
Add /healthz and /readyz (checks DB, Redis, OPA, LiteLLM). Add make targets dev, migrate, test, lint, typecheck, gateway-check.
Finish with tests for settings loading and migrations, and show me the commands to verify the "Done when" list.
```

### M2 — Graph core with fake models (weeks 2–3)

**Scope:** schemas (11.1), `RunState` and reducers (11.2), all parent-graph nodes and routing (11.3–11.4), subtask worker subgraph (11.5) with stub specialists, deterministic plan validation, task state machine, Taskiq jobs `start_run` and `resume_run`, run leases with heartbeat and reaper, `run_events` outbox, `POST /v1/tasks`, `GET /v1/tasks/{id}`, SSE endpoint, cancel.

**Done when:** graph tests cover every route with `ScriptedChatModel`; a task submitted via the API runs end to end on fakes and streams events; killing a worker mid-run (SIGKILL) results in completion by another worker with no duplicated steps (NFR-01).

```text
Read SPEC.md sections 4.1–4.4, 8, 11, 17 and 20.1. Implement milestone M2.
Use LangGraph 1.x APIs as installed: StateGraph with context_schema, Send for fan-out, the async Postgres checkpointer, durability="sync".
Build a ScriptedChatModel test double that returns scripted AIMessages including tool calls and structured outputs.
Implement run leases in Postgres (lease_owner, lease_expires_at, heartbeat, reaper job) as described in section 8.
Write graph tests named after FR IDs for every route in 11.3–11.5, plus a resilience test that kills a worker process mid-run.
Do not call any real model in tests.
```

### M3 — MCP tools and real specialists (week 4)

**Scope:** five MCP servers (12.3) with FastMCP, tool registry sync, `MultiServerMCPClient`, specialists via `create_agent` with Budget, Tool-limit, Injection-guard and Tracing middleware (Policy middleware stubbed to allow), sandbox hardening, SSRF guard, Tavily + SearXNG, recorded fixtures.

**Done when:** the showcase request (5.6) completes on the `local` profile minus the email approval step; sandbox tests prove no network, read-only root fs and the time limit; injection test pages are flagged.

```text
Read SPEC.md sections 12, 15.2, 15.3 and 16. Implement milestone M3.
Build each MCP server in mcp_servers/ with the official mcp SDK (FastMCP), Streamable HTTP at /mcp, /healthz, Pydantic-validated inputs, structured errors.
Check the installed langchain and langchain-mcp-adapters APIs before writing middleware; use built-in middleware only where it matches the spec exactly.
Implement the sandbox hardening and SSRF guard and prove each property with a test.
Add make record-fixtures and use fixtures in tests.
```

### M4 — Human-in-the-loop and policy (weeks 5–6)

**Scope:** OPA policies and tests (15.1), PolicyMiddleware with `interrupt()` for approval, all escalation triggers and levels (14.1), payload and decision schemas (14.2), the pause/resume sequence (14.3) including multiple parallel interrupts, escalation API, four-eyes rule, reviewer chat, Slack notifier, SLA jobs, audit log writes.

**Done when:** every trigger in 14.1 has a passing test; a run with two parallel escalations resumes correctly when they are answered in either order; a requester cannot approve their own escalation; OPA down means deny.

```text
Read SPEC.md sections 4.5, 14 and 15.1. Implement milestone M4.
Resume with Command(resume={interrupt_id: decision}) and reconcile the escalations table from aget_state(config).interrupts after every stream, as in 14.3.
Make every side effect before an interrupt idempotent (section 11.6) and add tests that resume twice to prove it.
Write opa test cases for every rule, including fail-closed behaviour in the Python client.
```

### M5 — Observability and cost (week 7)

**Scope:** Langfuse SDK + callback handler, custom OpenTelemetry spans, PII span processor, `llm_calls` and `tool_calls` accounting, budget enforcement end to end, Prometheus metrics (19.2), three Grafana dashboards, Trace and Costs API endpoints, Compose `observability` profile.

**Done when:** one showcase run shows a complete nested trace in Langfuse; app-computed cost is within ±5% of the gateway's; a budget-capped run pauses at 100%; dashboards render from live data.

```text
Read SPEC.md sections 4.7, 15.3 and 19. Implement milestone M5.
Copy Langfuse's upstream docker-compose services into infra/compose with pinned versions instead of writing them from scratch.
Verify the Langfuse SDK's current OpenTelemetry and LangChain integration in its docs before wiring it.
```

### M6 — Long-term memory (week 8)

**Scope:** memories repository, write path with validation, redaction and dedupe (13.2), hybrid retrieval with RRF and rerank (13.3), decay and access tracking, nightly consolidation, deletion rules (13.4), embedding-model guard (13.5), Memory API.

**Done when:** memory eval cases (20.2) show the seeded lesson retrieved in the top 8; deleting a memory also removes summaries derived from it; invalid facts and preferences are rejected.

```text
Read SPEC.md sections 4.6 and 13. Implement milestone M6.
Implement the retrieval SQL from 13.3 in the repository with bound parameters and test it against a seeded testcontainers database.
```

### M7 — Frontend and authentication (weeks 9–10)

**Scope:** Keycloak realm export with the four roles and demo users, JWT validation and RBAC dependencies, the ten screens (18.1), generated API client, SSE hook, plan DAG and trace views, decision view with schema-driven edit forms, Playwright journeys 5.1–5.5.

**Done when:** all five journeys pass in Playwright; keyboard-only navigation works on the decision view; no `any` types in the API layer.

```text
Read SPEC.md sections 2, 5, 17 and 18. Implement milestone M7.
Generate the API client from the running OpenAPI spec; never hand-write API types.
Apply the frontend-design and accessibility rules in 18.2.
```

### M8 — Replay, evals, hardening, deployment, demo (weeks 11–12)

**Scope:** checkpoints list, replay with state patch and diff (19.4), eval datasets and harness with judge calibration and confidence intervals (20.2), `eval.yml`, Helm chart and kind deployment (21.3), Trivy and gitleaks gates, `make demo`, README written as internal docs, architecture diagram, 5-minute demo recording.

**Done when:** Section 1 targets are measured and reported (met or honestly not met); the Helm chart deploys on kind; `make demo` reproduces the showcase offline.

```text
Read SPEC.md sections 19.4, 20, 21.3, 21.4 and 23. Implement milestone M8.
The eval report must show means with bootstrap 95% confidence intervals and the judge-vs-human kappa.
Write the README as onboarding docs for a new teammate: what it is, architecture, how to run, how to add a specialist or tool, the eval numbers.
```

## 23. Risks, decisions and definition of done

### 23.1 Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| An 8B local model plans and reviews poorly | Low eval scores, weak demo | Measure all three profiles; headline numbers from `hybrid`/`cloud`; keep plans small (≤ 12 subtasks) and validated deterministically. |
| Structured output fails on local models | Crashes in planning and review | JSON-schema mode with a function-calling fallback, one repair retry, Qwen3 thinking disabled for structured calls (16.3). |
| Library APIs move (LangGraph, LangChain middleware, LiteLLM, Langfuse) | Broken code from stale examples | Resolve versions at install, read installed source/docs before use, pin lockfiles, Renovate PRs with CI. |
| Resume duplicates side effects | Double emails or writes | Idempotency keys on tool calls and escalation rows, interrupt before side effects, resume-twice tests (11.6). |
| Laptop cannot run the full stack | Slow development | Compose profiles; host Ollama; observability only when needed. |
| Prompt injection through fetched pages | Unsafe actions | Untrusted wrapping, scanning, taint propagation, mandatory approval for side effects on tainted context, injection eval set with target 0. |
| Scope creep | Never finishing | Milestone gates; P2 items only after M8. |

### 23.2 Decisions log

| Decision | Chosen | Rejected and why |
| --- | --- | --- |
| Orchestrator | LangGraph 1.x | CrewAI, AutoGen: weaker durable state and HITL primitives; Temporal + custom agents: excellent durability but more moving parts for v1. |
| Durability and recovery | LangGraph Postgres checkpointer + Postgres run leases | Relying on broker redelivery alone: delivery semantics vary by broker; leases make recovery explicit and testable. |
| Long-term memory | Own pgvector + full-text table | LangGraph store (semantic only), a separate vector DB (one more service, harder joins and deletes). |
| Tools | MCP servers | In-process tools: no isolation, no reuse by other clients. |
| Model access | LiteLLM proxy with aliases | Direct SDKs per provider: provider logic leaks into agents. |
| Policy | OPA / Rego | Hard-coded checks: not auditable or testable as policy. |
| Tracing | OpenTelemetry + Langfuse self-hosted | LangSmith: closed source, self-hosting only on enterprise plans. |
| Task queue | Taskiq on Redis Streams | Celery: weaker asyncio story for an async-first codebase. |
| Frontend | React 19 + Vite SPA | Next.js: server rendering adds little to an authenticated internal tool. |

### 23.3 Definition of done for v1.0

- [ ] All P0 and P1 requirements implemented, each with a test named after its ID
- [ ] Section 1 metrics measured on the eval suite and published in the README, with confidence intervals
- [ ] Zero unsafe tool executions without approval on the injection eval set
- [ ] Worker-kill and full-restart resilience tests pass
- [ ] CI green: lint, types, unit, graph, policy, integration, e2e, Trivy, gitleaks
- [ ] Helm chart deploys on kind; Compose runs `make demo` offline
- [ ] README written as internal onboarding docs, with architecture diagram and "how to add a specialist or tool"
- [ ] 5-minute demo recording covering journey 5.6

### 23.4 Sources (opened October 2026)

- [LangGraph 1.0 general availability](https://changelog.langchain.com/announcements/ann_lxOZRCoFw2Qn9)
- [LangGraph durable execution docs](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [Langfuse self-hosting overview](https://langfuse.com/self-hosting)
- [Langfuse v4 stack components and versions](https://railway.com/deploy/langfuse-v4-llm-observability-stack)
- [pgvector 0.8.2 installation and Docker tags](https://docs.pgedge.com/pgvector/v0-8-2/additional-installation-methods/)
