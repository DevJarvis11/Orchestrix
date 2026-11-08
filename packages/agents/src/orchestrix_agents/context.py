"""Per-run dependencies (SPEC 11.2 RunContext). Immutable, never checkpointed."""

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel

from orchestrix_core.schemas import (
    Budget,
    CallTag,
    ErrorRecord,
    EscalationRecord,
    LlmCallRecord,
    MemoryHit,
    ModelPrice,
    Plan,
    PlannedSubtask,
    PolicyDecision,
    Review,
    Role,
    SubtaskResult,
    TaskInput,
    TaskStatus,
    Usage,
)


class ChatProvider(Protocol):
    """Hands out chat models by role; the concrete model per role is resolved per run (A2)."""

    def chat(self, role: Role, tag: CallTag | None = None) -> BaseChatModel:
        """A chat model for `role`. `tag` attributes its calls in llm_calls (A6)."""
        ...

    def model_name(self, role: Role) -> str: ...

    def price(self, role: Role) -> ModelPrice | None: ...


class SpecialistError(Exception):
    def __init__(self, message: str, *, retryable: bool, stop: str | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.stop = stop  # "budget_exceeded": a human chose to stop the run at budget_100


@dataclass(frozen=True)
class SpecialistRequest:
    subtask: PlannedSubtask
    inputs: dict[str, str]
    feedback: str = ""
    hint: str = ""
    tainted_inputs: bool = False  # an input came from a tainted subtask
    memories: list[MemoryHit] = field(default_factory=list[MemoryHit])
    attempt: int = 0
    revision: int = 0
    usage_so_far: Usage = field(default_factory=Usage)  # the run's usage before this call
    budget: Budget | None = None  # effective caps (a human may have raised them)


@dataclass(frozen=True)
class SpecialistOutcome:
    result: SubtaskResult
    usage: Usage = field(default_factory=Usage)
    tainted: bool = False  # untrusted content entered the context (SPEC 15.2)
    warnings: list[str] = field(default_factory=list[str])  # e.g. injection_suspected
    budget: Budget | None = None  # raised by a human at budget_100 inside the specialist
    escalations: list[EscalationRecord] = field(default_factory=list[EscalationRecord])


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


@dataclass(frozen=True)
class StoredToolCall:
    id: str
    output: str
    status: str


class ToolCallStore(Protocol):
    """tool_calls rows (FR-23) keyed by idempotency key (plan improvement 3)."""

    async def find(self, idempotency_key: str) -> StoredToolCall | None: ...

    async def record(
        self,
        *,
        run_id: str,
        subtask_key: str | None,
        idempotency_key: str,
        tool: str,
        args: dict[str, Any],
        output: str,
        status: str,
        latency_ms: int,
        error: str | None,
        policy_decision: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> str: ...


class PolicyGate(Protocol):
    """FR-90: every tool call is checked before it runs. Fails closed (deny)."""

    async def check_tool(
        self,
        *,
        run_id: str,
        tool: str,
        specialist: str,
        args: dict[str, Any],
        user_id: str,
        team_id: str,
        tainted: bool,
    ) -> PolicyDecision: ...

    async def record_denial(
        self, *, run_id: str, tool: str, args: dict[str, Any], reasons: list[str]
    ) -> None: ...


class LlmCallSink(Protocol):
    """Writes llm_calls rows (SPEC 15.3)."""

    async def record(self, call: LlmCallRecord) -> None: ...


@dataclass(frozen=True)
class RunDigest:
    """What the memory writer learns from (SPEC 13.2 step 1)."""

    task: TaskInput
    plan: Plan | None
    outcome: str  # completed | failed | ...
    reviews: dict[str, list[Review]]
    errors: list[ErrorRecord]
    escalations: list[EscalationRecord]
    sources: list[str]  # every URL or artifact id the run's results cited (FR-56 facts)
    user_words: list[str]  # task description + clarification answers (FR-56 preferences)
    deliverable_excerpt: str = ""


class MemoryStore(Protocol):
    """Long-term memory (SPEC 13), injected so the graph never imports the memory package."""

    async def retrieve(
        self, *, run_id: str, task: TaskInput, user_id: str, team_id: str
    ) -> list[MemoryHit]: ...

    async def mark_used(self, memory_ids: list[str]) -> None: ...

    async def write(
        self, *, run_id: str, user_id: str, team_id: str, digest: RunDigest, llm: ChatProvider
    ) -> list[str]: ...


class CancelCheck(Protocol):
    async def is_cancelled(self, run_id: str) -> bool: ...


@dataclass(frozen=True)
class Services:
    llm: ChatProvider
    specialists: SpecialistRunner
    recorder: RunRecorder
    cancel: CancelCheck
    tool_calls: ToolCallStore | None = None  # None in graph tests that use stub specialists
    policy: PolicyGate | None = None  # None = allow everything (graph tests)
    memory: MemoryStore | None = None  # None = no long-term memory (graph tests, fake models)


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
    # SPEC 12.1 default wall-clock limits per specialist, seconds (human waits excluded).
    specialist_seconds: dict[str, int] = field(
        default_factory=lambda: {"research": 240, "data_analyst": 300, "writer": 240, "coder": 360}
    )
    side_effects: str = "live"  # "simulated" on replays (SPEC 19.4 step 5)
    clarify_enabled: bool = True  # FR-15
    team_name: str = ""
    active_seconds_base: float = 0.0  # active time before this worker took the lease (FR-63)
    clock_start: float = field(default_factory=time.monotonic)

    def active_seconds(self) -> float:
        return self.active_seconds_base + (time.monotonic() - self.clock_start)

    escalation_levels: dict[str, str] = field(default_factory=dict[str, str])  # trigger -> level
    org_email_domain: str = ""
