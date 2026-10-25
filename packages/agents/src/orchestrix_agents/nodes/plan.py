"""plan: the supervisor produces a Plan via structured output (FR-10, FR-11 repair)."""

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.llm import StructuredOutputError, invoke_structured
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.prompting import render_prompt
from orchestrix_agents.specialists import SPECIALIST_TOOLS
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import ErrorRecord, Plan
from orchestrix_core.schemas.events import PlanEvent


async def plan(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    llm = ctx.services.llm
    repairing = bool(state.get("plan_errors"))
    prompt = render_prompt(
        "planner.j2",
        task=state["task"],
        memories=state.get("memories", []),
        specialists=SPECIALIST_TOOLS,
        errors=state.get("plan_errors", []) if repairing else [],
    )
    version = state.get("plan_version", 0) + 1
    try:
        result = await invoke_structured(
            llm.chat("planner"), Plan, [HumanMessage(prompt)], price=llm.price("planner")
        )
    except StructuredOutputError as exc:
        # validate_plan turns a missing plan into an error and routes to repair or a human.
        return {
            "plan_version": version,
            "plan_errors": [str(exc)],
            "errors": [ErrorRecord(node="plan", message=str(exc))],
            "usage": STEP,
        }
    await ctx.services.recorder.save_plan(ctx.run_id, version, result.value, edited_by=None)
    emit(
        runtime,
        PlanEvent(
            type="plan.created",
            run_id=ctx.run_id,
            version=version,
            confidence=result.value.confidence,
        ),
    )
    return {
        "plan": result.value,
        "plan_version": version,
        "plan_errors": [],
        "usage": STEP + result.usage,
    }
