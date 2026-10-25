"""Deterministic plan validation (FR-11)."""

import pytest

from orchestrix_agents.validation import find_cycle, validate_plan
from tests.fixtures.fakes import make_plan, subtask


def test_fr11_valid_plan_has_no_errors() -> None:
    plan = make_plan(subtask("s1"), subtask("s2", "writer", ["s1"], inputs={"notes": "s1.output"}))
    assert validate_plan(plan, []) == []


def test_fr11_missing_plan() -> None:
    assert validate_plan(None, []) == ["no valid plan was produced"]


def test_fr11_cycle_detected_with_kahn() -> None:
    plan = make_plan(
        subtask("s1", depends_on=["s3"]),
        subtask("s2", depends_on=["s1"]),
        subtask("s3", depends_on=["s2"]),
        subtask("s4"),
    )
    assert find_cycle(plan) == ["s1", "s2", "s3"]
    assert any("dependency cycle among s1, s2, s3" in e for e in validate_plan(plan, []))


@pytest.mark.parametrize(
    ("subtasks", "expected"),
    [
        ([subtask("s1", depends_on=["s9"])], "depends on unknown subtask s9"),
        ([subtask("s1", depends_on=["s1"])], "depends on itself"),
        ([subtask("s1"), subtask("s1")], "duplicate subtask key s1"),
        ([subtask("s1"), subtask("s2", inputs={"x": "s1.output"})], "s1 is not in depends_on"),
        ([subtask("s1", inputs={"x": "http://evil"})], "unresolvable reference"),
        (
            [subtask("s1", inputs={"csv": "attachment:missing.csv"})],
            "unknown attachment 'missing.csv'",
        ),
    ],
)
def test_fr11_rejects_invalid_plans(subtasks: list, expected: str) -> None:
    errors = validate_plan(make_plan(*subtasks), [])
    assert any(expected in e for e in errors), errors


def test_fr11_attachment_reference_allowed_when_attached() -> None:
    plan = make_plan(subtask("s1", "data_analyst", inputs={"csv": "attachment:latency.csv"}))
    assert validate_plan(plan, ["latency.csv"]) == []
