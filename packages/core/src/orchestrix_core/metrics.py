"""Prometheus metrics (SPEC 19.2), prefix orchestrix_. Each process exposes its own registry:
the API on /metrics, the worker on ORCHESTRIX_METRICS_PORT."""

from typing import Final

from prometheus_client import Counter, Gauge, Histogram

RUNS = Counter("orchestrix_runs_total", "Runs finished", ["status", "profile"])
RUN_DURATION = Histogram(
    "orchestrix_run_duration_seconds",
    "Active run time, excluding human wait",
    ["status"],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
NODE_DURATION = Histogram("orchestrix_node_duration_seconds", "Graph node duration", ["node"])
LLM_TOKENS = Counter("orchestrix_llm_tokens_total", "Model tokens", ["agent", "alias", "direction"])
LLM_COST = Counter("orchestrix_llm_cost_usd_total", "Model cost (USD)", ["agent", "alias", "team"])
TOOL_CALLS = Counter("orchestrix_tool_calls_total", "Tool calls", ["tool", "status"])
TOOL_LATENCY = Histogram("orchestrix_tool_latency_seconds", "Tool latency", ["tool"])
POLICY_DECISIONS = Counter(
    "orchestrix_policy_decisions_total", "Policy decisions", ["result", "tool"]
)
ESCALATIONS_OPEN = Gauge("orchestrix_escalations_open", "Open escalations", ["level"])
ESCALATIONS_OVERDUE = Gauge("orchestrix_escalations_overdue", "Open escalations past their SLA")
ESCALATION_WAIT = Histogram(
    "orchestrix_escalation_wait_seconds",
    "Time from escalation to decision",
    ["level", "trigger"],
    buckets=(10, 60, 300, 900, 3600, 4 * 3600, 24 * 3600, 72 * 3600),
)
REVIEW_SCORE = Histogram(
    "orchestrix_review_score", "Reviewer scores", ["specialist"], buckets=tuple(range(0, 11))
)
LEASE_RECLAIMS = Counter("orchestrix_run_lease_reclaims_total", "Worker crash recoveries")
QUEUE_DEPTH = Gauge("orchestrix_queue_depth", "Jobs waiting in the queue", ["job"])

LEVELS: Final = ("notify", "clarify", "approve_action", "approve_plan", "take_over")
