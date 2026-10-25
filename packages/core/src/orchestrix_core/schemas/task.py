"""Task intake (FR-02) and the task snapshot carried in run state."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from orchestrix_core.schemas.budget import Budget
from orchestrix_core.schemas.models import Role

TaskStatus = Literal[
    "queued",
    "planning",
    "awaiting_plan_approval",
    "running",
    "awaiting_human",
    "reviewing",
    "completed",
    "failed",
    "cancelled",
    "budget_exceeded",
]


class TaskCreate(BaseModel):
    """Body of POST /v1/tasks (FR-02). Attachments are uploaded first (M3) and referenced by id."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=8000)
    attachments: list[UUID] = Field(default=[], max_length=5)
    budget: Budget | None = None  # override; must not exceed the team cap
    require_plan_approval: bool = False
    model_overrides: dict[Role, str] = {}  # amendment A2: per-task model per role


class TaskInput(BaseModel):
    """What the graph knows about the task. Stored in RunState, so JSON-safe only."""

    id: str
    title: str
    description: str
    attachments: list[str] = []
    require_plan_approval: bool = False
