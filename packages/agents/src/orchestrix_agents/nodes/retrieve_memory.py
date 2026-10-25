"""retrieve_memory: hybrid memory search for planning (FR-52). Implemented in M6; empty until then."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_core.schemas.events import MemoryEvent


async def retrieve_memory(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    emit(runtime, MemoryEvent(type="memory.retrieved", run_id=runtime.context.run_id))
    return {"memories": state.get("memories", []), "usage": STEP}
