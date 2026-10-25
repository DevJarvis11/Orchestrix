"""Checkpoint serializer with an explicit allow-list (plan improvement 5).

Recent langgraph-checkpoint restricts msgpack deserialisation of unregistered types; every type
we put in RunState is registered here, and a round-trip test proves the list is complete.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from orchestrix_core.schemas import (
    Budget,
    ErrorRecord,
    EscalationRecord,
    Issue,
    MemoryHit,
    Plan,
    PlannedSubtask,
    Review,
    SubtaskResult,
    TaskInput,
    Usage,
)

ALLOWED_TYPES: tuple[type, ...] = (
    Budget,
    ErrorRecord,
    EscalationRecord,
    Issue,
    MemoryHit,
    Plan,
    PlannedSubtask,
    Review,
    SubtaskResult,
    TaskInput,
    Usage,
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


def make_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_TYPES)
