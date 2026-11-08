"""Run state and reducers (SPEC 11.2). Only JSON-safe data and Pydantic models; clients live in RunContext."""

import operator
from typing import Annotated, Literal, Required, TypedDict

from orchestrix_core.schemas import (
    Budget,
    ErrorRecord,
    EscalationRecord,
    MemoryHit,
    Plan,
    PlannedSubtask,
    Review,
    SubtaskResult,
    TaskInput,
    Usage,
)

SubtaskStatus = Literal[
    "pending", "running", "in_review", "approved", "failed", "taken_over", "skipped"
]
TERMINAL: frozenset[str] = frozenset({"approved", "failed", "taken_over", "skipped"})
DONE_OK: frozenset[str] = frozenset({"approved", "taken_over"})  # dependents may start

StopReason = Literal["cancelled", "budget_exceeded", "failed"]


def merge_dict[K, V](left: dict[K, V] | None, right: dict[K, V] | None) -> dict[K, V]:
    return {**(left or {}), **(right or {})}


def add_usage(left: Usage | None, right: Usage | None) -> Usage:
    return (left or Usage()) + (right or Usage())


def raise_budget(left: Budget | None, right: Budget | None) -> Budget:
    """Caps only go up: parallel workers may each report a human-approved raise (budget_100)."""
    if left is None or right is None:
        return left or right or Budget()
    a, b = left.model_dump(), right.model_dump()
    return Budget.model_validate({k: max(a[k], b[k]) for k in a})


class RunState(TypedDict, total=False):
    task: Required[TaskInput]
    budget: Annotated[
        Budget | None, raise_budget
    ]  # effective caps; humans may raise them (budget_100)
    memories: list[MemoryHit]
    plan: Plan
    plan_version: int
    plan_repairs: int
    plan_errors: list[str]  # validate_plan output; empty = valid
    subtask_status: Annotated[dict[str, SubtaskStatus], merge_dict]
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dict]
    reviews: Annotated[dict[str, list[Review]], merge_dict]
    deliverable: str
    final_review: Review
    final_revisions: int
    final_outcome: Literal["pass", "revise", "escalate"]  # final_review -> final_gate
    clarify_questions: list[str]  # FR-15: pending questions for the requester
    clarifications: list[str]  # answered Q&A: the requester's own words
    clarified: bool  # the supervisor asked (or decided not to ask) once
    stop_reason: StopReason  # set by dispatch/escalations; routes straight to finalize
    tainted: Annotated[dict[str, bool], merge_dict]  # subtask -> untrusted content seen (SPEC 15.2)
    usage: Annotated[Usage, add_usage]
    errors: Annotated[list[ErrorRecord], operator.add]
    escalations: Annotated[list[EscalationRecord], operator.add]


class WorkerState(TypedDict, total=False):
    """Subtask worker subgraph state (SPEC 11.5). Shares the reducer keys with RunState."""

    subtask: Required[PlannedSubtask]
    plan_version: int
    inputs: dict[str, str]  # resolved input name -> content
    tainted_inputs: bool  # an input came from a tainted subtask
    usage_before: Usage  # the run's usage when this worker started (budget checks)
    budget_now: Budget  # the run's effective caps when this worker started
    memories: list[MemoryHit]
    attempts: int
    revisions: int
    feedback: str  # reviewer or human note for the next execute
    hint: str  # "alternative approach" hint after a failure
    result: SubtaskResult
    last_review: Review
    outcome: Literal["review", "retry", "escalate", "done"]  # where the worker goes next
    escalate_trigger: str
    stop_reason: StopReason  # a human chose fail_task
    budget: Annotated[Budget | None, raise_budget]
    tainted: Annotated[dict[str, bool], merge_dict]
    subtask_status: Annotated[dict[str, SubtaskStatus], merge_dict]
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dict]
    reviews: Annotated[dict[str, list[Review]], merge_dict]
    usage: Annotated[Usage, add_usage]
    errors: Annotated[list[ErrorRecord], operator.add]
    escalations: Annotated[list[EscalationRecord], operator.add]


class WorkerOutput(TypedDict, total=False):
    """What the subgraph hands back to the parent: the shared keys only."""

    stop_reason: StopReason
    budget: Annotated[Budget | None, raise_budget]
    tainted: Annotated[dict[str, bool], merge_dict]
    subtask_status: Annotated[dict[str, SubtaskStatus], merge_dict]
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dict]
    reviews: Annotated[dict[str, list[Review]], merge_dict]
    usage: Annotated[Usage, add_usage]
    errors: Annotated[list[ErrorRecord], operator.add]
    escalations: Annotated[list[EscalationRecord], operator.add]
