"""RunState reducers (SPEC 11.2) and the checkpoint serializer allow-list (improvement 5)."""

from orchestrix_agents.serde import make_serializer
from orchestrix_agents.state import add_usage, merge_dict
from orchestrix_core.schemas import (
    Budget,
    ErrorRecord,
    EscalationRecord,
    MemoryHit,
    Review,
    SubtaskResult,
    TaskInput,
    Usage,
)
from tests.fixtures.fakes import make_plan, subtask


def test_merge_dict_right_wins_and_handles_none() -> None:
    assert merge_dict({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}
    assert merge_dict(None, {"a": 1}) == {"a": 1}


def test_add_usage_sums_and_handles_none() -> None:
    assert add_usage(None, Usage(steps=1)) == Usage(steps=1)
    assert add_usage(Usage(steps=1, usd=0.5), Usage(steps=2, usd=0.25)) == Usage(steps=3, usd=0.75)


def test_run_state_round_trips_through_the_serializer() -> None:
    state = {
        "task": TaskInput(id="t", title="x", description="y", attachments=["a.csv"]),
        "budget": Budget(max_usd=1.5),
        "memories": [MemoryHit(id="m1", kind="lesson", text="cite sources", score=0.8)],
        "plan": make_plan(subtask("s1"), subtask("s2", depends_on=["s1"])),
        "subtask_status": {"s1": "approved", "s2": "skipped"},
        "subtask_results": {
            "s1": SubtaskResult(key="s1", output="o", sources=["https://x"], confidence=0.7)
        },
        "reviews": {"s1": [Review(verdict="approve", score=8, feedback="ok")]},
        "usage": Usage(input_tokens=5, usd=0.01, steps=3),
        "errors": [ErrorRecord(node="execute", subtask="s2", message="boom")],
        "escalations": [
            EscalationRecord(
                trigger="subtask_failed_twice", level="take_over", subtask="s2", resolution="reject"
            )
        ],
    }
    serde = make_serializer()
    restored = serde.loads_typed(serde.dumps_typed(state))
    assert restored == state
    assert type(restored["plan"]) is type(state["plan"])
