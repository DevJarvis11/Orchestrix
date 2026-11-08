"""LLM call accounting (SPEC 15.3, FR-62, amendment A6)."""

from typing import Literal

from pydantic import BaseModel

Agent = Literal[
    "supervisor", "research", "data_analyst", "writer", "coder", "reviewer", "memory_writer"
]
Purpose = Literal[
    "plan",
    "plan_repair",
    "execute",
    "revision",
    "retry",
    "review",
    "synthesis",
    "final_review",
    "memory",
    "chat",
]


class CallTag(BaseModel):
    """Who made a model call and why; attached to the chat model as metadata (A6)."""

    agent: Agent
    purpose: Purpose
    subtask_key: str | None = None
    attempt: int = 0
    revision: int = 0


class LlmCallRecord(CallTag):
    run_id: str
    model_alias: str  # the role asked for (planner, specialist, ...)
    requested_model: str  # the catalogue model the role resolved to
    model_id: str  # the model that actually served (differs after a fallback)
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    trace_id: str | None = None
    span_id: str | None = None
