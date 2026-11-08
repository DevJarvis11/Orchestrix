"""review: the reviewer scores a subtask result (FR-30, FR-31). Max 2 revisions, then escalate."""

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.llm import invoke_structured
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.prompting import render_prompt
from orchestrix_agents.state import WorkerState
from orchestrix_core.schemas import CallTag, Review
from orchestrix_core.schemas.events import ReviewEvent, SubtaskEvent

MAX_REVISIONS = 2


async def review_subtask(state: WorkerState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    llm = ctx.services.llm
    subtask = state["subtask"]
    result = state.get("result")
    if result is None:
        raise ValueError(f"review of {subtask.key} reached without a result")
    prompt = render_prompt("reviewer.j2", subtask=subtask, result=result)
    structured = await invoke_structured(
        llm.chat(
            "reviewer",
            CallTag(
                agent="reviewer",
                purpose="review",
                subtask_key=subtask.key,
                attempt=state.get("attempts", 0),
                revision=state.get("revisions", 0),
            ),
        ),
        Review,
        [HumanMessage(prompt)],
        price=llm.price("reviewer"),
    )
    review = structured.value
    history = [*state.get("reviews", {}).get(subtask.key, []), review]
    await ctx.services.recorder.save_review(
        ctx.run_id,
        subtask.key,
        len(history) - 1,
        review,
        structured.model or llm.model_name("reviewer"),
    )
    emit(
        runtime,
        ReviewEvent(
            type="review.completed",
            run_id=ctx.run_id,
            subtask=subtask.key,
            verdict=review.verdict,
            score=review.score,
            specialist=subtask.specialist,
        ),
    )
    update: dict[str, Any] = {
        "last_review": review,
        "reviews": {subtask.key: history},
        "usage": STEP + structured.usage,
    }
    revisions = state.get("revisions", 0)
    if review.verdict == "approve" and review.score >= ctx.review_score_threshold:
        await ctx.services.recorder.save_subtask(
            ctx.run_id,
            state.get("plan_version", 1),
            subtask,
            "approved",
            attempts=state.get("attempts", 0),
            revisions=revisions,
            result=result,
        )
        emit(
            runtime,
            SubtaskEvent(
                type="subtask.status", run_id=ctx.run_id, subtask=subtask.key, status="approved"
            ),
        )
        return update | {"outcome": "done", "subtask_status": {subtask.key: "approved"}}
    if review.verdict != "escalate" and revisions < MAX_REVISIONS:
        # An "approve" below the score threshold is treated as a revision request (FR-40).
        return update | {
            "outcome": "retry",
            "revisions": revisions + 1,
            "feedback": review.feedback,
            "hint": "",
        }
    return update | {"outcome": "escalate", "escalate_trigger": "low_review_score"}


def route_after_review(state: WorkerState) -> str:
    return {"done": "__end__", "retry": "execute"}.get(state.get("outcome", ""), "escalate")
