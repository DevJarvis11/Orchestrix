"""execute: run the specialist on one subtask (FR-21, FR-24). Two failures escalate."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext, SpecialistError, SpecialistRequest
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import WorkerState
from orchestrix_core.schemas import ErrorRecord
from orchestrix_core.schemas.events import SubtaskEvent

MAX_ATTEMPTS = 2
ALTERNATIVE_HINT = "Use a different method or source than last time; keep the same deliverable."


async def execute_subtask(state: WorkerState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    subtask = state["subtask"]
    attempts = state.get("attempts", 0)
    try:
        outcome = await ctx.services.specialists.run(
            SpecialistRequest(
                subtask=subtask,
                inputs=state.get("inputs", {}),
                feedback=state.get("feedback", ""),
                hint=state.get("hint", ""),
            ),
            ctx,
        )
    except SpecialistError as exc:
        attempts += 1
        error = ErrorRecord(
            node="execute", subtask=subtask.key, message=str(exc), retryable=exc.retryable
        )
        update: dict[str, Any] = {"attempts": attempts, "errors": [error], "usage": STEP}
        if attempts < MAX_ATTEMPTS:
            return update | {"outcome": "retry", "hint": ALTERNATIVE_HINT}
        return update | {"outcome": "escalate", "escalate_trigger": "subtask_failed_twice"}

    result = outcome.result.model_copy(update={"key": subtask.key})
    await ctx.services.recorder.save_subtask(
        ctx.run_id,
        state.get("plan_version", 1),
        subtask,
        "in_review",
        attempts=attempts,
        revisions=state.get("revisions", 0),
        result=result,
    )
    emit(runtime, SubtaskEvent(type="subtask.result", run_id=ctx.run_id, subtask=subtask.key))
    return {
        "outcome": "review",
        "result": result,
        "subtask_status": {subtask.key: "in_review"},
        "subtask_results": {subtask.key: result},
        "usage": STEP + outcome.usage,
    }


def route_after_execute(state: WorkerState) -> str:
    return {"review": "review", "retry": "execute"}.get(state.get("outcome", ""), "escalate")
