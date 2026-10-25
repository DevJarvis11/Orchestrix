"""Per-run budget caps (FR-63)."""

from pydantic import BaseModel, Field


class Budget(BaseModel):
    """Caps for one run. Defaults are the FR-63 values."""

    max_tokens: int = Field(default=400_000, ge=1)
    max_usd: float = Field(default=2.0, ge=0)
    max_seconds: int = Field(default=30 * 60, ge=1)  # active time, excluding human wait
    max_tool_calls: int = Field(default=60, ge=1)
    max_steps: int = Field(default=150, ge=1)
