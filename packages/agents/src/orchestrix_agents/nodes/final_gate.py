"""final_gate: human decisions before delivery (SPEC 14.1): a deliverable the reviewer would not
pass (low_review_score), or a task whose requester asked to approve it (user_requested_review)."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, ask_human, plan_progress
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import EscalationPayload, EscalationRecord, Review


async def final_gate(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    review = state.get("final_review") or Review(verdict="escalate", score=0, feedback="")
    deliverable = state.get("deliverable", "")
    if state.get("final_outcome") == "pass":
        if not state["task"].require_final_approval:
            return {"usage": STEP}
        trigger = "user_requested_review"
        title = f"Approve the deliverable before delivery (review score {review.score:.1f})"
    else:
        trigger = "low_review_score"
        title = f"Final review scored {review.score:.1f}: deliver as-is, rewrite, or fail"
    level = ctx.escalation_levels.get(trigger, "approve_action")
    decision = ask_human(
        EscalationPayload(
            trigger=trigger,
            level=level,  # type: ignore[arg-type]
            title=title,
            task_summary=state["task"].title,
            plan_progress=plan_progress(state.get("plan"), dict(state.get("subtask_status", {}))),
            proposed={"deliverable": deliverable, "review": review.model_dump(mode="json")},
            reasoning=review.feedback,
            related_memories=[{"id": m.id, "text": m.text} for m in state.get("memories", [])],
            allowed_actions=["approve", "take_over", "reject"],
        )
    )
    record = EscalationRecord(
        trigger=trigger,
        level=level,  # type: ignore[arg-type]
        resolution=decision.action,
    )
    update: dict[str, Any] = {"escalations": [record], "usage": STEP}
    if decision.action == "reject":
        return update | {"stop_reason": "failed"}
    # Paused as awaiting_human; delivery continues from the review phase (FR-01).
    await ctx.services.recorder.set_task_status(ctx.task_id, "reviewing")
    if decision.action == "take_over":
        update["deliverable"] = decision.output or ""
    # Accepted by a human: deliver without another automated pass.
    return update | {"final_review": review.model_copy(update={"verdict": "approve"})}


def route_after_final_gate(state: RunState) -> str:
    return "finalize" if state.get("stop_reason") else "write_memory"
