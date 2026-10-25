"""validate_plan: deterministic checks (FR-11). Invalid plans get one repair, then a human."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_agents.validation import validate_plan as check
from orchestrix_core.schemas import ErrorRecord
from orchestrix_core.schemas.events import PlanEvent


async def validate_plan(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    # A plan the planner could not produce arrives as plan_errors with no new plan.
    errors = list(state.get("plan_errors", [])) or check(
        state.get("plan"), state["task"].attachments
    )
    if not errors:
        return {"plan_errors": [], "usage": STEP}
    emit(
        runtime,
        PlanEvent(
            type="plan.invalid",
            run_id=ctx.run_id,
            version=state.get("plan_version", 0),
            errors=errors,
        ),
    )
    return {
        "plan_errors": errors,
        "plan_repairs": state.get("plan_repairs", 0) + 1,
        "errors": [ErrorRecord(node="validate_plan", message="; ".join(errors))],
        "usage": STEP,
    }


def route_after_validate(state: RunState) -> str:
    """SPEC 11.4: one repair, then the second failure goes to a human via plan_gate."""
    if not state.get("plan_errors"):
        return "plan_gate"
    return "plan" if state.get("plan_repairs", 0) <= 1 else "plan_gate"
