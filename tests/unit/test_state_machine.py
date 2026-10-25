"""Task status transitions (FR-01)."""

import itertools

import pytest

from orchestrix_core.state_machine import TERMINAL, TRANSITIONS, InvalidTransition, check_transition

HAPPY = [
    "queued",
    "planning",
    "awaiting_plan_approval",
    "running",
    "awaiting_human",
    "reviewing",
    "completed",
]


def test_fr01_happy_path_is_legal() -> None:
    for current, target in itertools.pairwise(HAPPY):
        assert check_transition(current, target) is True  # type: ignore[arg-type]


def test_fr01_optional_states_can_be_skipped() -> None:
    assert check_transition("planning", "running")
    assert check_transition("running", "reviewing")


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_fr01_terminal_states_are_final(terminal: str) -> None:
    assert TRANSITIONS[terminal] == frozenset()  # type: ignore[index]
    with pytest.raises(InvalidTransition):
        check_transition(terminal, "running")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("current", "target"),
    [("queued", "completed"), ("reviewing", "planning"), ("completed", "failed")],
)
def test_fr01_illegal_transitions_raise(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition, match=f"{current} -> {target}"):
        check_transition(current, target)  # type: ignore[arg-type]


def test_fr01_repeating_current_status_is_a_noop() -> None:
    assert check_transition("running", "running") is False  # nodes re-run on resume


@pytest.mark.parametrize("status", [s for s in TRANSITIONS if s not in TERMINAL])
def test_fr01_every_live_status_can_stop(status: str) -> None:
    for stop in ("failed", "cancelled", "budget_exceeded"):
        assert check_transition(status, stop)  # type: ignore[arg-type]
