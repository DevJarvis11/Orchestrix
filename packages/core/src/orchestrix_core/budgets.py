"""Budget accounting and enforcement (FR-63, SPEC 15.3)."""

from typing import Final, Literal

from pydantic import BaseModel

from orchestrix_core.schemas.budget import Budget
from orchestrix_core.schemas.run import Usage

WARNING_RATIO: Final = 0.8

BudgetLevel = Literal["ok", "warning", "exceeded"]


class BudgetStatus(BaseModel):
    level: BudgetLevel
    metric: str | None = None  # the cap that is closest to (or past) its limit
    used: float = 0
    cap: float = 0


def check_budget(usage: Usage, budget: Budget, *, active_seconds: float = 0) -> BudgetStatus:
    """Compare usage to every cap; report the most-consumed one.

    `exceeded` means a cap is reached (>= 100%): the run must stop or ask a human (FR-40).
    """
    pairs: list[tuple[str, float, float]] = [
        ("tokens", usage.tokens, budget.max_tokens),
        ("usd", usage.usd, budget.max_usd),
        ("seconds", active_seconds, budget.max_seconds),
        ("tool_calls", usage.tool_calls, budget.max_tool_calls),
        ("steps", usage.steps, budget.max_steps),
    ]
    metric, used, cap = max(pairs, key=lambda p: p[1] / p[2] if p[2] else 0.0)
    ratio = used / cap if cap else 0.0
    level: BudgetLevel = "exceeded" if ratio >= 1 else "warning" if ratio >= WARNING_RATIO else "ok"
    return BudgetStatus(level=level, metric=metric, used=used, cap=cap)
