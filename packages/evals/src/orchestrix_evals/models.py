"""Dataset schemas (SPEC 20.2). Datasets are versioned YAML files under `datasets/`."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from orchestrix_core.schemas import PlannedSubtask, SubtaskResult

DATASETS = Path(__file__).parent / "datasets"
ATTACHMENTS = DATASETS / "attachments"

Category = Literal["research", "analysis", "writing", "coding", "mixed"]


class Check(BaseModel):
    """One deterministic check on a deliverable (see checks.py for each kind)."""

    kind: Literal[
        "sections",  # every `values` item appears as a markdown heading
        "contains_all",  # every `values` item appears (case-insensitive)
        "contains_any",  # at least one `values` item appears
        "min_citations",  # at least `n` URLs
        "citations_resolve",  # every URL cited appears in the run's subtask sources
        "csv_number",  # an aggregate of an attached CSV appears in the text
        "code_block",  # a fenced code block is present
        "words_between",  # word count in [min, max]
    ]
    values: list[str] = []
    n: int = 0
    min: int = 0
    max: int = 1_000_000
    csv: str | None = None
    column: str | None = None
    agg: Literal["mean", "median", "max", "min", "sum", "count"] = "mean"
    where: dict[str, str] = {}  # column -> value filter
    decimals: list[int] = [0, 1, 2]  # accepted roundings


class Golden(BaseModel):
    id: str
    category: Category
    title: str
    description: str
    attachments: list[str] = []
    rubric: list[str] = Field(min_length=1)
    checks: list[Check] = []
    clarify_answers: list[str] = []  # if the supervisor asks (FR-15)


class Defect(BaseModel):
    id: str
    defect: Literal[
        "unsourced_claim",
        "wrong_number",
        "missing_section",
        "off_topic",
        "format",
        "fabricated_source",
    ]
    subtask: PlannedSubtask
    result: SubtaskResult
    must_mention: list[str] = Field(min_length=1)  # the reviewer names the defect (any keyword)


class EscalationCase(BaseModel):
    id: str
    title: str
    description: str
    needed: bool
    triggers: list[str] = []  # the triggers that should fire when needed
    require_plan_approval: bool = False
    require_final_approval: bool = False
    budget: dict[str, Any] | None = None
    attachments: list[str] = []
    clarify_answers: list[str] = []


class InjectionCase(BaseModel):
    id: str
    technique: str
    title: str
    description: str
    page_name: str  # uploaded as this attachment name (user-uploaded content taints, 15.2)
    page: str  # the hostile document


class MemoryCase(BaseModel):
    id: str
    lesson: str  # what A should teach
    a_title: str
    a_description: str
    b_title: str
    b_description: str
    keywords: list[str] = []  # a retrieved A-memory should mention one of these


class Suite(BaseModel):
    golden: list[Golden]
    defects: list[Defect]
    escalations: list[EscalationCase]
    injections: list[InjectionCase]
    memory: list[MemoryCase]


def _load(name: str) -> list[dict[str, Any]]:
    data = yaml.safe_load((DATASETS / name).read_text())
    return list(data["cases"])


@lru_cache
def load_suite() -> Suite:
    return Suite(
        golden=[Golden.model_validate(c) for c in _load("golden.yaml")],
        defects=[Defect.model_validate(c) for c in _load("defects.yaml")],
        escalations=[EscalationCase.model_validate(c) for c in _load("escalations.yaml")],
        injections=[InjectionCase.model_validate(c) for c in _load("injection.yaml")],
        memory=[MemoryCase.model_validate(c) for c in _load("memory.yaml")],
    )
