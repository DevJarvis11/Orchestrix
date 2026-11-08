"""Trigger -> level mapping and SLA timings (SPEC 14.1, 14.5), from escalation_policy.yaml."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Level = Literal["notify", "clarify", "approve_action", "approve_plan", "take_over"]
DEFAULT_PATH = Path(__file__).parent / "escalation_policy.yaml"


class EscalationPolicy(BaseModel):
    levels: dict[str, Level]
    sla_hours: float = Field(default=24, gt=0)  # re-notify open escalations after this
    timeout_hours: float = Field(default=72, gt=0)  # then fail the task: escalation_timeout
    check_minutes: float = Field(default=15, gt=0)


@lru_cache
def load_escalation_policy(path: Path | None = None) -> EscalationPolicy:
    return EscalationPolicy.model_validate(yaml.safe_load((path or DEFAULT_PATH).read_text()))
