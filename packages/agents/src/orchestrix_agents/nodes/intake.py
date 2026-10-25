"""intake: start the run (SPEC 11.3)."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_core.schemas.events import RunLifecycleEvent


async def intake(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    if "task" not in state:
        raise ValueError("run started without a task")
    await ctx.services.recorder.set_task_status(ctx.task_id, "planning")
    emit(runtime, RunLifecycleEvent(type="run.started", run_id=ctx.run_id))
    return {
        "budget": state.get("budget", ctx.budget),
        "plan_version": state.get("plan_version", 0),
        "plan_repairs": state.get("plan_repairs", 0),
        "final_revisions": state.get("final_revisions", 0),
        "usage": STEP,
    }
