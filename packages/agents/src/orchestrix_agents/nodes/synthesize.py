"""synthesize: merge approved results into the deliverable (SPEC 11.3)."""

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.llm import strip_think, usage_of
from orchestrix_agents.nodes._shared import STEP, require_plan
from orchestrix_agents.prompting import render_prompt
from orchestrix_agents.state import DONE_OK, RunState
from orchestrix_core.schemas import CallTag


async def synthesize(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    llm = ctx.services.llm
    await ctx.services.recorder.set_task_status(ctx.task_id, "reviewing")
    statuses = state.get("subtask_status", {})
    results = {
        k: r for k, r in state.get("subtask_results", {}).items() if statuses.get(k) in DONE_OK
    }
    final = state.get("final_review")
    prompt = render_prompt(
        "synthesizer.j2",
        task=state["task"],
        plan=require_plan(state),
        results=results,
        skipped=sorted(k for k, s in statuses.items() if s in ("skipped", "failed")),
        feedback=final.feedback
        if final is not None and state.get("final_revisions", 0) > 0
        else "",
    )
    message = await llm.chat("planner", CallTag(agent="supervisor", purpose="synthesis")).ainvoke(
        [HumanMessage(prompt)]
    )
    content = message.content if isinstance(message.content, str) else str(message.content)
    return {
        "deliverable": strip_think(content),
        "usage": STEP + usage_of(message, llm.price("planner")),
    }
