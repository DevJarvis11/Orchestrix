"""write_memory: extract long-term memories (FR-51, FR-56; SPEC 13.2). Never fails the task.

Re-running it is harmless: memory ids are derived from (run, kind, text) and duplicates are
reinforced instead of inserted. Memories the plan cited are marked used here, once per run
(FR-53), rather than at planning time, where a repaired or edited plan would count twice.
"""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext, RunDigest
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import ErrorRecord
from orchestrix_core.schemas.events import MemoryEvent


def digest_of(state: RunState) -> RunDigest:
    task = state["task"]
    sources = sorted({src for r in state.get("subtask_results", {}).values() for src in r.sources})
    return RunDigest(
        task=task,
        plan=state.get("plan"),
        outcome=state.get("stop_reason") or "completed",
        reviews=dict(state.get("reviews", {})),
        errors=list(state.get("errors", [])),
        escalations=list(state.get("escalations", [])),
        sources=sources,
        user_words=[task.description, *state.get("clarifications", [])],
        deliverable_excerpt=state.get("deliverable", "")[:2000],
    )


async def write_memory(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    store = ctx.services.memory
    memory_ids: list[str] = []
    try:
        if store is not None:
            plan = state.get("plan")
            if plan is not None and plan.used_memory_ids:
                await store.mark_used(plan.used_memory_ids)
            memory_ids = await store.write(
                run_id=ctx.run_id,
                user_id=ctx.user_id,
                team_id=ctx.team_id,
                digest=digest_of(state),
                llm=ctx.services.llm,
            )
        emit(
            runtime,
            MemoryEvent(type="memory.written", run_id=ctx.run_id, memory_ids=memory_ids),
        )
    except Exception as exc:  # memory is best-effort: a failure here never fails the run
        return {
            "errors": [ErrorRecord(node="write_memory", message=str(exc)[:500])],
            "usage": STEP,
        }
    return {"usage": STEP}
