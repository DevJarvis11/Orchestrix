"""Streaming events (SPEC 17.2): one discriminated union, written to run_events then Redis."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from orchestrix_core.schemas.run import EscalationLevel, Usage


class _Event(BaseModel):
    run_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunLifecycleEvent(_Event):
    type: Literal["run.started", "run.resumed", "run.completed", "run.failed", "run.cancelled"]
    reason: str | None = None
    usage: Usage | None = None


class MemoryEvent(_Event):
    type: Literal["memory.retrieved", "memory.written"]
    memory_ids: list[str] = []


class PlanEvent(_Event):
    type: Literal["plan.created", "plan.invalid", "plan.approved", "plan.edited"]
    version: int
    confidence: float | None = None
    errors: list[str] = []


class SubtaskEvent(_Event):
    type: Literal["subtask.started", "subtask.result", "subtask.status"]
    subtask: str
    status: str | None = None
    specialist: str | None = None


class LlmCompletedEvent(_Event):
    type: Literal["llm.completed"]
    agent: str
    model_alias: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ToolEvent(_Event):
    type: Literal["tool.called", "tool.completed", "tool.denied"]
    tool: str
    subtask: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    reason: str | None = None


class ReviewEvent(_Event):
    type: Literal["review.completed"]
    subtask: str | None  # None = final review
    verdict: str
    score: float
    specialist: str | None = None


class EscalationEvent(_Event):
    type: Literal["escalation.created", "escalation.resolved"]
    interrupt_id: str
    trigger: str
    level: EscalationLevel
    subtask: str | None = None
    actor: str | None = None  # who resolved it ("resumed by <reviewer>", journey 5.2)
    action: str | None = None


class BudgetEvent(_Event):
    type: Literal["budget.warning", "budget.exceeded"]
    metric: str
    used: float
    cap: float


class DeliverableEvent(_Event):
    type: Literal["deliverable.ready"]
    artifact_id: str | None = None
    chars: int


RunEvent = Annotated[
    RunLifecycleEvent
    | MemoryEvent
    | PlanEvent
    | SubtaskEvent
    | LlmCompletedEvent
    | ToolEvent
    | ReviewEvent
    | EscalationEvent
    | BudgetEvent
    | DeliverableEvent,
    Field(discriminator="type"),
]

run_event_adapter: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
