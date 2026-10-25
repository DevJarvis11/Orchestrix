"""finalize: store the deliverable and close the task (SPEC 11.3), or record why the run stopped."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_core.schemas.events import DeliverableEvent, RunLifecycleEvent


async def finalize(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    recorder = ctx.services.recorder
    usage = (state.get("usage") or STEP) + STEP
    stop = state.get("stop_reason")
    if stop == "cancelled":
        await recorder.set_task_status(ctx.task_id, "cancelled")
        emit(runtime, RunLifecycleEvent(type="run.cancelled", run_id=ctx.run_id, usage=usage))
    elif stop is not None:
        await recorder.set_task_status(ctx.task_id, stop, reason=stop)
        emit(
            runtime,
            RunLifecycleEvent(type="run.failed", run_id=ctx.run_id, reason=stop, usage=usage),
        )
    else:
        content = state.get("deliverable", "")
        artifact_id = await recorder.save_deliverable(ctx.run_id, ctx.task_id, content)
        emit(
            runtime,
            DeliverableEvent(
                type="deliverable.ready",
                run_id=ctx.run_id,
                artifact_id=artifact_id,
                chars=len(content),
            ),
        )
        await recorder.set_task_status(ctx.task_id, "completed")
        emit(runtime, RunLifecycleEvent(type="run.completed", run_id=ctx.run_id, usage=usage))
    return {"usage": STEP}
