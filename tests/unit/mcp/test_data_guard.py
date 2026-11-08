"""data.sql_query guard (SPEC 12.3): one SELECT, no DDL/DML, forced LIMIT."""

import pytest

from orchestrix_mcp_data.guard import ROW_LIMIT, RejectedSQL, guarded_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, avg(ms) FROM latency GROUP BY region",
        "WITH t AS (SELECT * FROM latency) SELECT * FROM t",
        "SELECT * FROM latency UNION SELECT * FROM latency_archive",
        "select 1",
    ],
)
def test_selects_are_wrapped_with_a_limit(sql: str) -> None:
    out = guarded_select(sql)
    assert out.startswith("SELECT * FROM (")
    assert out.endswith(f"LIMIT {ROW_LIMIT + 1}")


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM latency", "only SELECT"),
        ("UPDATE latency SET ms = 0", "only SELECT"),
        ("DROP TABLE latency", "only SELECT"),
        ("SELECT 1; SELECT 2", "exactly one statement"),
        ("SELECT * INTO copy FROM latency", "Into"),
        ("SELECT * FROM latency FOR UPDATE", "Lock"),
        ("WITH d AS (DELETE FROM latency RETURNING *) SELECT * FROM d", "Delete"),
        ("COPY latency TO '/tmp/x'", "only SELECT"),
        ("SELEC * FRM", "only SELECT"),  # sqlglot reads unknown text as an opaque Command
        ("SELECT (1", "parse"),
    ],
)
def test_everything_else_is_rejected(sql: str, reason: str) -> None:
    with pytest.raises(RejectedSQL, match=reason):
        guarded_select(sql)
