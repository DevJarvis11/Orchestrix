"""Task status transitions (FR-01). Illegal transitions raise; re-applying the current status is a no-op."""

from typing import Final

from orchestrix_core.schemas.task import TaskStatus


def _s(*statuses: TaskStatus) -> frozenset[TaskStatus]:
    return frozenset(statuses)


TERMINAL: Final = _s("completed", "failed", "cancelled", "budget_exceeded")

# queued -> planning -> awaiting_plan_approval? -> running -> awaiting_human? -> reviewing -> completed
_STOPS: Final = _s("failed", "cancelled", "budget_exceeded")
TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    "queued": _s("planning") | _STOPS,
    # planning can pause for a human: low confidence or a plan that failed repair (FR-11, FR-13)
    "planning": _s("awaiting_plan_approval", "awaiting_human", "running") | _STOPS,
    "awaiting_plan_approval": _s("planning", "running") | _STOPS,
    "running": _s("awaiting_human", "reviewing", "planning") | _STOPS,  # planning = FR-14
    "awaiting_human": _s("planning", "running", "reviewing") | _STOPS,
    "reviewing": _s("awaiting_human", "completed") | _STOPS,
    "completed": _s(),
    "failed": _s(),
    "cancelled": _s(),
    "budget_exceeded": _s(),
}


class InvalidTransition(ValueError):
    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        super().__init__(f"illegal task transition {current} -> {target}")
        self.current = current
        self.target = target


def check_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Return True if the status changes, False if it is already `target`; raise if illegal.

    Nodes re-run on resume (SPEC 11.6), so repeating the current status must be harmless.
    """
    if current == target:
        return False
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(current, target)
    return True
