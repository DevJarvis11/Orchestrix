"""Model roles and pricing (SPEC 16, amendment A2)."""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["planner", "specialist", "reviewer", "fast"]
"""What an agent asks for. Resolved to a concrete gateway model per task (A2)."""


class ModelPrice(BaseModel):
    """USD per million tokens. Notional for self-hosted models (improvement 8)."""

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
