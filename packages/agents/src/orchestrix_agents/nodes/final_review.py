"""final_review: check the deliverable against the request (FR-33). One revision, then a human."""

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.llm import invoke_structured
from orchestrix_agents.nodes._shared import STEP, ask_human, emit, plan_progress
from orchestrix_agents.prompting import render_prompt
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import EscalationPayload, EscalationRecord, Review
from orchestrix_core.schemas.events import ReviewEvent

MAX_FINAL_REVISIONS = 1


async def final_review(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    llm = ctx.services.llm
    await ctx.services.recorder.set_task_status(ctx.task_id, "reviewing")
    deliverable = state.get("deliverable", "")
    prompt = render_prompt("final_reviewer.j2", task=state["task"], deliverable=deliverable)
    result = await invoke_structured(
        llm.chat("reviewer"), Review, [HumanMessage(prompt)], price=llm.price("reviewer")
    )
    review = result.value
    revisions = state.get("final_revisions", 0)
    await ctx.services.recorder.save_review(
        ctx.run_id, None, revisions, review, result.model or llm.model_name("reviewer")
    )
    emit(
        runtime,
        ReviewEvent(
            type="review.completed",
            run_id=ctx.run_id,
            subtask=None,
            verdict=review.verdict,
            score=review.score,
        ),
    )
    update: dict[str, Any] = {"final_review": review, "usage": STEP + result.usage}
    passed = review.verdict == "approve" and review.score >= ctx.review_score_threshold
    if passed:
        return update
    if review.verdict == "revise" and revisions < MAX_FINAL_REVISIONS:
        return update | {"final_revisions": revisions + 1}

    decision = ask_human(
        EscalationPayload(
            trigger="low_review_score",
            level="approve_action",
            title=f"Final review scored {review.score:.1f}: deliver as-is, rewrite, or fail",
            task_summary=state["task"].title,
            plan_progress=plan_progress(state.get("plan"), dict(state.get("subtask_status", {}))),
            proposed={"deliverable": deliverable, "review": review.model_dump(mode="json")},
            reasoning=review.feedback,
            related_memories=[],
            allowed_actions=["approve", "take_over", "reject"],
        )
    )
    record = EscalationRecord(
        trigger="low_review_score", level="approve_action", resolution=decision.action
    )
    update["escalations"] = [record]
    if decision.action == "reject":
        return update | {"stop_reason": "failed"}
    if decision.action == "take_over":
        update["deliverable"] = decision.output or ""
    # Accepted by a human: deliver without another automated pass.
    return update | {"final_review": review.model_copy(update={"verdict": "approve"})}


def route_final(state: RunState) -> str:
    if state.get("stop_reason"):
        return "finalize"
    review = state.get("final_review")
    if review is not None and review.verdict == "approve":
        return "write_memory"
    return "synthesize"
