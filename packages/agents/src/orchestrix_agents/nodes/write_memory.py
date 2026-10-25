"""write_memory: extract long-term memories (FR-51). Implemented in M6; never fails the task."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, emit
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import ErrorRecord
from orchestrix_core.schemas.events import MemoryEvent


async def write_memory(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    try:
        memory_ids: list[str] = []  # M6: extract, validate (FR-56), redact, dedupe, insert
        emit(
            runtime,
            MemoryEvent(
                type="memory.written", run_id=runtime.context.run_id, memory_ids=memory_ids
            ),
        )
    except Exception as exc:  # memory is best-effort: a failure here never fails the run
        return {"errors": [ErrorRecord(node="write_memory", message=str(exc))], "usage": STEP}
    return {"usage": STEP}
