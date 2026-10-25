"""Run-state records (SPEC 11.2): usage, errors, escalations, memory hits."""

from typing import Literal

from pydantic import BaseModel, Field

EscalationLevel = Literal["notify", "clarify", "approve_action", "approve_plan", "take_over"]


class Usage(BaseModel):
    """Resources consumed by a run. Summed by the `add_usage` reducer."""

    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    tool_calls: int = 0
    steps: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            usd=round(self.usd + other.usd, 6),
            tool_calls=self.tool_calls + other.tool_calls,
            steps=self.steps + other.steps,
        )


class ErrorRecord(BaseModel):
    node: str
    message: str
    subtask: str | None = None
    retryable: bool = False


class EscalationRecord(BaseModel):
    """A human decision point and how it was resolved. Full payload schema arrives in M4 (14.2)."""

    trigger: str
    level: EscalationLevel
    subtask: str | None = None
    resolution: str | None = None  # the decision action once resumed


class MemoryHit(BaseModel):
    id: str
    kind: Literal["episode", "lesson", "preference", "fact"]
    text: str
    score: float = Field(ge=0)
