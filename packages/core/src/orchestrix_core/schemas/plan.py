"""Planner, specialist and reviewer outputs (SPEC 11.1). Every model here drives control flow."""

from typing import Literal

from pydantic import BaseModel, Field

Specialist = Literal["research", "data_analyst", "writer", "coder"]
SPECIALISTS: tuple[Specialist, ...] = ("research", "data_analyst", "writer", "coder")


class PlannedSubtask(BaseModel):
    key: str = Field(pattern=r"^s\d{1,2}$")
    description: str = Field(max_length=1000)
    specialist: Specialist
    depends_on: list[str] = []
    inputs: dict[str, str] = {}  # name -> reference, e.g. "s1.output" or "attachment:latency.csv"
    expected_output: Literal["markdown", "json", "table", "code", "file"]
    acceptance_criteria: list[str] = Field(min_length=1, max_length=6)
    complexity: Literal["low", "medium", "high"]
    max_tool_calls: int = Field(default=8, ge=1, le=20)


class Plan(BaseModel):
    goal: str
    subtasks: list[PlannedSubtask] = Field(min_length=1, max_length=12)
    deliverable_format: Literal["markdown_report", "json", "table", "code"]
    confidence: float = Field(ge=0, le=1)
    confidence_reason: str
    used_memory_ids: list[str] = []
    estimated_cost_usd: float = Field(ge=0)


class SubtaskResult(BaseModel):
    key: str
    output: str  # markdown, JSON text or code
    artifacts: list[str] = []  # artifact ids
    sources: list[str] = []  # URLs or artifact ids backing claims
    tool_call_ids: list[str] = []
    confidence: float = Field(ge=0, le=1)
    notes: str = ""


class Issue(BaseModel):
    description: str
    severity: Literal["low", "medium", "high", "critical"]
    quote: str | None = None  # exact excerpt from the output


class Review(BaseModel):
    verdict: Literal["approve", "revise", "escalate"]
    score: float = Field(ge=0, le=10)
    issues: list[Issue] = []
    feedback: str
