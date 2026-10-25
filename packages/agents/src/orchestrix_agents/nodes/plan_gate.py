"""plan_gate: human approval of the plan (FR-13, FR-43; SPEC 14.1 approve_plan / take_over)."""

from typing import Any

from langgraph.runtime import Runtime
from pydantic import ValidationError

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, ask_human, emit, plan_progress
from orchestrix_agents.state import RunState
from orchestrix_agents.validation import validate_plan
from orchestrix_core.schemas import (
    DecisionAction,
    EscalationPayload,
    EscalationRecord,
    Plan,
)
from orchestrix_core.schemas.events import PlanEvent


async def plan_gate(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    plan = state.get("plan")
    errors = state.get("plan_errors", [])
    task = state["task"]

    if errors:
        trigger, level = "plan_invalid_twice", "take_over"
        allowed: list[DecisionAction] = ["approve_with_edits", "take_over", "reject"]
        title = "The planner could not produce a valid plan: fix or write it"
    elif task.require_plan_approval:
        trigger, level = "plan_approval_required", "approve_plan"
        allowed = ["approve", "approve_with_edits", "reject"]
        title = "Approve the plan before any subtask runs"
    elif plan is not None and plan.confidence < ctx.plan_confidence_threshold:
        trigger, level = "plan_low_confidence", "approve_plan"
        allowed = ["approve", "approve_with_edits", "reject"]
        title = f"Low plan confidence ({plan.confidence:.2f}): approve or edit"
    else:
        return {"usage": STEP}

    decision = ask_human(
        EscalationPayload(
            trigger=trigger,
            level=level,
            title=title,
            task_summary=f"{task.title}: {task.description[:500]}",
            plan_progress=plan_progress(plan, {}),
            proposed={"plan": plan.model_dump(mode="json") if plan else None, "errors": errors},
            reasoning=plan.confidence_reason if plan else "; ".join(errors),
            related_memories=[{"id": m.id, "text": m.text} for m in state.get("memories", [])],
            allowed_actions=allowed,
        )
    )
    record = EscalationRecord(trigger=trigger, level=level, resolution=decision.action)

    if decision.action == "reject":
        return {"stop_reason": "failed", "escalations": [record], "usage": STEP}

    if decision.action == "approve":
        emit(
            runtime,
            PlanEvent(
                type="plan.approved", run_id=ctx.run_id, version=state.get("plan_version", 0)
            ),
        )
        return {"plan_errors": [], "escalations": [record], "usage": STEP}

    # approve_with_edits (edits = plan JSON) or take_over (output = plan JSON): re-validate (14.2).
    raw = decision.edits if decision.action == "approve_with_edits" else decision.output
    try:
        edited = (
            Plan.model_validate(raw)
            if isinstance(raw, dict)
            else Plan.model_validate_json(raw or "")
        )
    except ValidationError as exc:
        new_errors = [f"edited plan is not a valid Plan: {exc.error_count()} error(s)"]
        return {"plan_errors": new_errors, "escalations": [record], "usage": STEP}
    new_errors = validate_plan(edited, task.attachments)
    version = state.get("plan_version", 0) + 1
    if not new_errors:
        await ctx.services.recorder.save_plan(
            ctx.run_id, version, edited, edited_by=decision.actor_id
        )
        emit(runtime, PlanEvent(type="plan.edited", run_id=ctx.run_id, version=version))
    return {
        "plan": edited,
        "plan_version": version,
        "plan_errors": new_errors,  # still invalid -> plan_gate asks again
        "escalations": [record],
        "usage": STEP,
    }


def route_after_gate(state: RunState) -> str:
    if state.get("stop_reason"):
        return "finalize"
    return "plan_gate" if state.get("plan_errors") else "dispatch"
