"""Budget maths (FR-63)."""

import pytest

from orchestrix_core.budgets import check_budget
from orchestrix_core.schemas import Budget, Usage


def test_fr63_under_80_percent_is_ok() -> None:
    assert check_budget(Usage(input_tokens=100), Budget()).level == "ok"


@pytest.mark.parametrize(
    ("usage", "metric"),
    [
        (Usage(input_tokens=330_000), "tokens"),
        (Usage(usd=1.7), "usd"),
        (Usage(tool_calls=50), "tool_calls"),
        (Usage(steps=125), "steps"),
    ],
)
def test_fr63_warning_reports_the_closest_cap(usage: Usage, metric: str) -> None:
    status = check_budget(usage, Budget())
    assert status.level == "warning"
    assert status.metric == metric


def test_fr63_reaching_a_cap_is_exceeded() -> None:
    status = check_budget(Usage(tool_calls=60), Budget())
    assert (status.level, status.metric, status.used, status.cap) == (
        "exceeded",
        "tool_calls",
        60,
        60,
    )


def test_fr63_active_seconds_count() -> None:
    assert check_budget(Usage(), Budget(), active_seconds=1800).metric == "seconds"


def test_usage_adds_every_field() -> None:
    total = Usage(input_tokens=1, output_tokens=2, usd=0.1, tool_calls=1, steps=1) + Usage(
        input_tokens=3, output_tokens=4, usd=0.2, tool_calls=2, steps=5
    )
    assert total == Usage(input_tokens=4, output_tokens=6, usd=0.3, tool_calls=3, steps=6)
    assert total.tokens == 10
