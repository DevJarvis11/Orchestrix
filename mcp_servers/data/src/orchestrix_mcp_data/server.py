"""mcp-data: data.describe_table and data.sql_query over the read-only demo_data schema (SPEC 12.3)."""

import datetime as dt
import decimal
import uuid
from typing import Annotated, Any, LiteralString, cast

import psycopg
from mcp.server.fastmcp import FastMCP
from psycopg import sql as pgsql
from pydantic import BaseModel, Field, SecretStr

from orchestrix_mcp_data.guard import ROW_LIMIT, RejectedSQL, guarded_select
from orchestrix_tools.conventions import new_server, tool_error
from orchestrix_tools.server_settings import ServerSettings

STATEMENT_TIMEOUT = "10s"
SCHEMA = "demo_data"


class DataSettings(ServerSettings):
    readonly_database_url: SecretStr  # libpq DSN for the orchestrix_readonly role


class Column(BaseModel):
    name: str
    type: str
    nullable: bool


class TableDescription(BaseModel):
    table: str
    columns: list[Column]


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _json_safe(value: Any) -> Any:
    if isinstance(value, decimal.Decimal | uuid.UUID | dt.date | dt.time | dt.timedelta):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


async def _connect(dsn: str) -> psycopg.AsyncConnection[Any]:
    conn = await psycopg.AsyncConnection.connect(dsn)
    await conn.execute("SET TRANSACTION READ ONLY")
    await conn.execute(
        pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(STATEMENT_TIMEOUT))
    )
    await conn.execute(pgsql.SQL("SET LOCAL search_path = {}").format(pgsql.Identifier(SCHEMA)))
    return conn


def build_server(settings: DataSettings) -> FastMCP:
    mcp = new_server(
        "data", f"Read-only SQL over the {SCHEMA} schema. SELECT only, max {ROW_LIMIT} rows."
    )
    dsn = settings.readonly_database_url.get_secret_value()

    @mcp.tool(
        name="describe_table", description=f"List the columns and types of a table in {SCHEMA}."
    )
    async def describe_table(  # pyright: ignore[reportUnusedFunction]
        table: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")],
    ) -> TableDescription:
        conn = await _connect(dsn)
        try:
            cur = await conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (SCHEMA, table),
            )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        if not rows:
            raise tool_error("table_not_found", f"no table {table!r} in {SCHEMA}")
        return TableDescription(
            table=table,
            columns=[Column(name=r[0], type=r[1], nullable=r[2] == "YES") for r in rows],
        )

    @mcp.tool(
        name="sql_query", description=f"Run one SELECT over {SCHEMA}; at most {ROW_LIMIT} rows."
    )
    async def sql_query(sql: Annotated[str, Field(min_length=1, max_length=20_000)]) -> QueryResult:  # pyright: ignore[reportUnusedFunction]
        try:
            query = guarded_select(sql)
        except RejectedSQL as exc:
            raise tool_error("sql_rejected", str(exc)) from exc
        conn = await _connect(dsn)
        try:
            # The query is rebuilt from a validated sqlglot AST (guarded_select), never raw input.
            cur = await conn.execute(cast(LiteralString, query))
            rows = await cur.fetchall()
            columns = [d.name for d in cur.description or []]
        except psycopg.errors.QueryCanceled as exc:
            raise tool_error(
                "timeout", f"query exceeded {STATEMENT_TIMEOUT}", retryable=False
            ) from exc
        except psycopg.Error as exc:
            raise tool_error("sql_error", str(exc).splitlines()[0]) from exc
        finally:
            await conn.close()
        return QueryResult(
            columns=columns,
            rows=[[_json_safe(v) for v in row] for row in rows[:ROW_LIMIT]],
            truncated=len(rows) > ROW_LIMIT,
        )

    return mcp
