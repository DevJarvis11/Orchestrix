"""In-memory Services for graph tests: no database, Redis or model gateway."""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from orchestrix_agents.context import (
    RunContext,
    Services,
    SpecialistError,
    SpecialistOutcome,
    SpecialistRequest,
)
from orchestrix_core.schemas import (
    Budget,
    CallTag,
    ModelPrice,
    Plan,
    PlannedSubtask,
    Review,
    Role,
    SubtaskResult,
    TaskStatus,
    Usage,
)
from orchestrix_core.state_machine import check_transition
from tests.fixtures.scripted_model import Reply, ScriptedChatModel


class FakeChatProvider:
    def __init__(self, **models: ScriptedChatModel) -> None:
        self.models: dict[str, ScriptedChatModel] = models

    def chat(self, role: Role, tag: CallTag | None = None) -> BaseChatModel:
        if role not in self.models:
            raise AssertionError(f"test did not script the {role} model")
        return self.models[role]

    def model_name(self, role: Role) -> str:
        return f"fake-{role}"

    def price(self, role: Role) -> ModelPrice | None:
        return ModelPrice(input_per_mtok=1.0, output_per_mtok=2.0)


@dataclass
class FakeSpecialists:
    """Outcomes per subtask key, consumed in order. A SpecialistError entry simulates a failure."""

    outcomes: dict[str, list[SubtaskResult | SpecialistError]] = field(default_factory=dict)
    requests: list[SpecialistRequest] = field(default_factory=list)
    default: Callable[[SpecialistRequest], SubtaskResult] | None = None

    async def run(self, request: SpecialistRequest, ctx: RunContext) -> SpecialistOutcome:
        self.requests.append(request)
        queue = self.outcomes.get(request.subtask.key)
        if queue:
            item = queue.pop(0)
        elif self.default is not None:
            item = self.default(request)
        else:
            item = SubtaskResult(
                key=request.subtask.key, output=f"result of {request.subtask.key}", confidence=0.9
            )
        if isinstance(item, SpecialistError):
            raise item
        return SpecialistOutcome(result=item, usage=Usage(input_tokens=10, output_tokens=5))


@dataclass
class FakeRecorder:
    statuses: list[TaskStatus] = field(default_factory=list)
    plans: dict[int, Plan] = field(default_factory=dict)
    subtasks: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    reviews: dict[tuple[str | None, int], Review] = field(default_factory=dict)
    deliverable: str | None = None

    async def set_task_status(
        self, task_id: str, status: TaskStatus, reason: str | None = None
    ) -> None:
        """Enforces FR-01 like TaskRepo.set_status: an illegal transition raises."""
        current: TaskStatus = self.statuses[-1] if self.statuses else "queued"
        if check_transition(current, status):
            self.statuses.append(status)

    async def save_plan(self, run_id: str, version: int, plan: Plan, edited_by: str | None) -> None:
        self.plans[version] = plan

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
    ) -> None:
        self.subtasks[subtask.key].append(status)

    async def save_review(
        self, run_id: str, subtask_key: str | None, ordinal: int, review: Review, model: str
    ) -> None:
        self.reviews[(subtask_key, ordinal)] = review

    async def save_deliverable(self, run_id: str, task_id: str, content: str) -> str | None:
        self.deliverable = content
        return "artifact-1"


@dataclass
class FakeCancel:
    cancelled: bool = False

    async def is_cancelled(self, run_id: str) -> bool:
        return self.cancelled


SUBTASK_RE = re.compile(r"subtask (s\d+)", re.IGNORECASE)


def subtask_key(messages: list[BaseMessage]) -> str:
    """Which subtask a reviewer prompt is about (prompts name it: 'subtask s2')."""
    text = " ".join(str(m.content) for m in messages)
    match = SUBTASK_RE.search(text)
    return match.group(1) if match else "final"


def keyed_responder(replies: dict[str, list[Reply]]) -> Callable[[list[BaseMessage]], Reply]:
    """Replies per subtask key (or 'final'), each consumed in order; the last one repeats."""

    def respond(messages: list[BaseMessage]) -> Reply:
        queue = replies[subtask_key(messages)]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return respond


def make_context(
    llm: FakeChatProvider,
    specialists: FakeSpecialists | None = None,
    *,
    budget: Budget | None = None,
    max_concurrency: int = 4,
    cancel: FakeCancel | None = None,
) -> tuple[RunContext, FakeRecorder]:
    recorder = FakeRecorder()
    services = Services(
        llm=llm,
        specialists=specialists or FakeSpecialists(),
        recorder=recorder,
        cancel=cancel or FakeCancel(),
    )
    ctx = RunContext(
        run_id="run-1",
        task_id="task-1",
        user_id="user-1",
        team_id="team-1",
        budget=budget or Budget(),
        services=services,
        max_concurrency=max_concurrency,
        clarify_enabled=False,  # scripted model sequences predate FR-15; tests opt in
    )
    return ctx, recorder


def approve(score: float = 8.5) -> Review:
    return Review(verdict="approve", score=score, feedback="looks good")


def revise(feedback: str = "add sources") -> Review:
    return Review(verdict="revise", score=4.0, feedback=feedback)


def subtask(
    key: str, specialist: str = "research", depends_on: list[str] | None = None, **kw: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "description": f"do {key}",
        "specialist": specialist,
        "depends_on": depends_on or [],
        "expected_output": "markdown",
        "acceptance_criteria": [f"{key} is complete"],
        "complexity": "low",
        **kw,
    }


def make_plan(*subtasks: dict[str, Any], confidence: float = 0.9) -> Plan:
    return Plan.model_validate(
        {
            "goal": "answer the question",
            "subtasks": list(subtasks),
            "deliverable_format": "markdown_report",
            "confidence": confidence,
            "confidence_reason": "clear request",
            "estimated_cost_usd": 0.1,
        }
    )
