"""Per-run dependencies (SPEC 11.2 RunContext). Immutable, never checkpointed."""

from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.language_models import BaseChatModel

from orchestrix_core.schemas import (
    Budget,
    ModelPrice,
    Plan,
    PlannedSubtask,
    Review,
    Role,
    SubtaskResult,
    TaskStatus,
    Usage,
)


class ChatProvider(Protocol):
    """Hands out chat models by role; the concrete model per role is resolved per run (A2)."""

    def chat(self, role: Role) -> BaseChatModel: ...

    def model_name(self, role: Role) -> str: ...

    def price(self, role: Role) -> ModelPrice | None: ...


class SpecialistError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class SpecialistRequest:
    subtask: PlannedSubtask
    inputs: dict[str, str]
    feedback: str = ""
    hint: str = ""


@dataclass(frozen=True)
class SpecialistOutcome:
    result: SubtaskResult
    usage: Usage = field(default_factory=Usage)


class SpecialistRunner(Protocol):
    """Runs one specialist on one subtask. M2: stub; M3: create_agent with MCP tools."""

    async def run(self, request: SpecialistRequest, ctx: "RunContext") -> SpecialistOutcome: ...


class RunRecorder(Protocol):
    """Idempotent writes of graph progress into app tables (SPEC 11.6). Nodes re-run on resume."""

    async def set_task_status(
        self, task_id: str, status: TaskStatus, reason: str | None = None
    ) -> None: ...

    async def save_plan(
        self, run_id: str, version: int, plan: Plan, edited_by: str | None
    ) -> None: ...

    async def save_subtask(
        self,
        run_id: str,
        plan_version: int,
        subtask: PlannedSubtask,
        status: str,
        *,
        attempts: int = 0,
        revisions: int = 0,
        result: SubtaskResult | None = None,
    ) -> None: ...

    async def save_review(
        self, run_id: str, subtask_key: str | None, ordinal: int, review: Review, model: str
    ) -> None: ...

    async def save_deliverable(self, run_id: str, task_id: str, content: str) -> str | None: ...


class CancelCheck(Protocol):
    async def is_cancelled(self, run_id: str) -> bool: ...


@dataclass(frozen=True)
class Services:
    llm: ChatProvider
    specialists: SpecialistRunner
    recorder: RunRecorder
    cancel: CancelCheck


@dataclass(frozen=True)
class RunContext:
    run_id: str
    task_id: str
    user_id: str
    team_id: str
    budget: Budget
    services: Services
    max_concurrency: int = 4
    plan_confidence_threshold: float = 0.6
    review_score_threshold: float = 6.0
