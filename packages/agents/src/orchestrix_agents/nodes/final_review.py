"""final_review: the reviewer checks the deliverable against the request (FR-33). Model only:
the human decisions it may need happen in `final_gate`, so a resume never re-asks the model
before replaying a human answer (SPEC 11.6)."""

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.llm import invoke_structured
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.prompting import render_prompt
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import CallTag, Review
from orchestrix_core.schemas.events import ReviewEvent

MAX_FINAL_REVISIONS = 1


async def final_review(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    llm = ctx.services.llm
    await ctx.services.recorder.set_task_status(ctx.task_id, "reviewing")
    deliverable = state.get("deliverable", "")
    prompt = render_prompt("final_reviewer.j2", task=state["task"], deliverable=deliverable)
    result = await invoke_structured(
        llm.chat("reviewer", CallTag(agent="reviewer", purpose="final_review")),
        Review,
        [HumanMessage(prompt)],
        price=llm.price("reviewer"),
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
    if not passed and review.verdict == "revise" and revisions < MAX_FINAL_REVISIONS:
        return update | {"final_revisions": revisions + 1, "final_outcome": "revise"}
    return update | {"final_outcome": "pass" if passed else "escalate"}


def route_final(state: RunState) -> str:
    return "synthesize" if state.get("final_outcome") == "revise" else "final_gate"
