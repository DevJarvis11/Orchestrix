"""retrieve_memory: hybrid memory search for planning (FR-12, FR-52; SPEC 13.3).
Memory is best-effort: a failure here plans without memories rather than failing the run."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import ErrorRecord
from orchestrix_core.schemas.events import MemoryEvent


async def retrieve_memory(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    store = ctx.services.memory
    hits = list(state.get("memories", []))
    errors: list[ErrorRecord] = []
    if store is not None and not hits:
        try:
            hits = await store.retrieve(
                run_id=ctx.run_id, task=state["task"], user_id=ctx.user_id, team_id=ctx.team_id
            )
        except Exception as exc:
            errors.append(ErrorRecord(node="retrieve_memory", message=str(exc)[:500]))
    emit(
        runtime,
        MemoryEvent(type="memory.retrieved", run_id=ctx.run_id, memory_ids=[m.id for m in hits]),
    )
    return {"memories": hits, "errors": errors, "usage": STEP}
