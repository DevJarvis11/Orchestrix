"""Event schemas (SPEC 17.2): one discriminated union."""

import pytest
from pydantic import ValidationError

from orchestrix_core.schemas.events import PlanEvent, ToolEvent, run_event_adapter


def test_fr06_events_round_trip_by_type() -> None:
    event = run_event_adapter.validate_python(
        {"type": "plan.created", "run_id": "r", "version": 1, "confidence": 0.8}
    )
    assert isinstance(event, PlanEvent)
    tool = run_event_adapter.validate_json(
        '{"type": "tool.completed", "run_id": "r", "tool": "web.fetch", "latency_ms": 812}'
    )
    assert isinstance(tool, ToolEvent)
    assert tool.latency_ms == 812


def test_fr06_unknown_event_type_rejected() -> None:
    with pytest.raises(ValidationError):
        run_event_adapter.validate_python({"type": "plan.exploded", "run_id": "r"})
