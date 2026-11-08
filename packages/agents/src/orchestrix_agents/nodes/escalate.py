"""escalate: a human decides a stuck subtask (SPEC 11.5, 14.1). Other subtasks keep their results."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, ask_human, emit
from orchestrix_agents.state import SubtaskStatus, WorkerState
from orchestrix_core.schemas import (
    DecisionAction,
    EscalationPayload,
    EscalationRecord,
    SubtaskResult,
)
from orchestrix_core.schemas.events import SubtaskEvent


async def escalate(state: WorkerState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    subtask = state["subtask"]
    trigger = state.get("escalate_trigger") or "low_review_score"
    result = state.get("result")
    review = state.get("last_review")
    if trigger == "subtask_failed_twice":
        level = "take_over"
        allowed: list[DecisionAction] = ["take_over", "reject"]
        title = f"Subtask {subtask.key} failed twice: take it over, retry, skip or fail"
        reasoning = "; ".join(e.message for e in state.get("errors", [])[-2:])
    else:
        level = "approve_action"
        allowed = ["approve", "take_over", "reject"]
        score = f"{review.score:.1f}" if review else "n/a"
        title = f"Subtask {subtask.key} scored {score} after revisions: accept, take over or reject"
        reasoning = review.feedback if review else ""

    decision = ask_human(
        EscalationPayload(
            trigger=trigger,
            level=level,
            title=title,
            task_summary=subtask.description,
            plan_progress=[
                {"key": subtask.key, "specialist": subtask.specialist, "status": "running"}
            ],
            proposed={
                "subtask": subtask.model_dump(mode="json"),
                "draft": result.model_dump(mode="json") if result else None,
                "review": review.model_dump(mode="json") if review else None,
            },
            reasoning=reasoning,
            related_memories=[{"id": m.id, "text": m.text} for m in state.get("memories", [])],
            allowed_actions=allowed,
            subtask=subtask.key,
        )
    )
    if decision.on_reject != "fail_task":  # paused as awaiting_human; back to running (FR-01)
        await ctx.services.recorder.set_task_status(ctx.task_id, "running")
    record = EscalationRecord(
        trigger=trigger, level=level, subtask=subtask.key, resolution=decision.action
    )
    base: dict[str, Any] = {"escalations": [record], "usage": STEP}

    if decision.action == "reject" and decision.on_reject == "retry":
        return base | {
            "outcome": "retry",
            "attempts": 0,
            "feedback": decision.reason or "",
            "escalate_trigger": "",
        }

    status: SubtaskStatus
    update: dict[str, Any] = {}
    if decision.action == "approve" and result is not None:
        status = "approved"
    elif decision.action == "take_over":
        status = "taken_over"
        result = (result or SubtaskResult(key=subtask.key, output="", confidence=1.0)).model_copy(
            update={"output": decision.output or "", "notes": f"written by {decision.actor_id}"}
        )
        update["subtask_results"] = {subtask.key: result}
    else:  # reject with skip, fail_task or no mode
        status = "skipped"
        if decision.on_reject == "fail_task":
            update["stop_reason"] = "failed"
    await ctx.services.recorder.save_subtask(
        ctx.run_id,
        state.get("plan_version", 1),
        subtask,
        status,
        attempts=state.get("attempts", 0),
        revisions=state.get("revisions", 0),
        result=result,
    )
    emit(
        runtime,
        SubtaskEvent(type="subtask.status", run_id=ctx.run_id, subtask=subtask.key, status=status),
    )
    return base | update | {"outcome": "done", "subtask_status": {subtask.key: status}}


def route_after_escalate(state: WorkerState) -> str:
    return "execute" if state.get("outcome") == "retry" else "__end__"
