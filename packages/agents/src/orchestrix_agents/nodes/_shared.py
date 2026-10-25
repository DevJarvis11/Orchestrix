"""Helpers shared by graph nodes: events, step accounting, human escalations."""

from collections.abc import Mapping
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel

from orchestrix_agents.context import RunContext
from orchestrix_core.schemas import (
    EscalationDecision,
    EscalationPayload,
    Plan,
    Usage,
    parse_decision,
)
from orchestrix_core.schemas.events import run_event_adapter

STEP = Usage(steps=1)


def emit(runtime: Runtime[RunContext], event: BaseModel | dict[str, Any]) -> None:
    """Send a SPEC 17.2 event on the custom stream; the worker persists and publishes it."""
    data = event.model_dump(mode="json") if isinstance(event, BaseModel) else event
    run_event_adapter.validate_python(data)  # fail fast on malformed events
    runtime.stream_writer(data)


def require_plan(state: Mapping[str, Any]) -> Plan:
    plan = state.get("plan")
    if not isinstance(plan, Plan):
        raise ValueError("node reached without a plan")
    return plan


def plan_progress(plan: Plan | None, statuses: dict[str, str]) -> list[dict[str, Any]]:
    if plan is None:
        return []
    return [
        {"key": s.key, "specialist": s.specialist, "status": statuses.get(s.key, "pending")}
        for s in plan.subtasks
    ]


def ask_human(payload: EscalationPayload) -> EscalationDecision:
    """Pause the run (SPEC 14.3). Code before this call re-runs on resume, so keep it idempotent."""
    value = interrupt(payload.model_dump(mode="json"))
    return parse_decision(value, payload.allowed_actions)
