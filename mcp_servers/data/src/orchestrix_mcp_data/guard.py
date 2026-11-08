"""SQL guard for data.sql_query (SPEC 12.3): one read-only SELECT, parsed and checked with sqlglot."""

from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ROW_LIMIT: Final = 1000
_FORBIDDEN: Final[tuple[type, ...]] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,  # COPY, SET, GRANT, VACUUM, ... parse as Command
    exp.Into,  # SELECT ... INTO creates a table
    exp.Lock,  # SELECT ... FOR UPDATE takes row locks
)


class RejectedSQL(ValueError):
    pass


def guarded_select(sql: str) -> str:
    """Return the query wrapped with a forced LIMIT, or raise RejectedSQL."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except ParseError as exc:
        raise RejectedSQL(f"could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise RejectedSQL("exactly one statement is allowed")
    [statement] = statements
    if not isinstance(statement, exp.Query):
        raise RejectedSQL("only SELECT queries are allowed")
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN):
            raise RejectedSQL(f"{type(node).__name__} is not allowed")
    # Built from the validated AST, not by string formatting. +1 row detects truncation.
    wrapped = exp.select("*").from_(statement.subquery("q")).limit(ROW_LIMIT + 1)
    return wrapped.sql(dialect="postgres")
