"""mcp-data against Postgres as the read-only role (SPEC 12.3)."""

import psycopg
import pytest
from pydantic import SecretStr

from orchestrix_mcp_data.server import DataSettings, build_server
from tests.integration.conftest import Database
from tests.unit.mcp.conftest import call, error_of

pytestmark = pytest.mark.integration


@pytest.fixture
async def data_server(database: Database):
    with psycopg.connect(database.url("migrator", driver=False), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS demo_data.latency")
        conn.execute("CREATE TABLE demo_data.latency (region text, ms int)")
        conn.execute("INSERT INTO demo_data.latency SELECT 'eu', g FROM generate_series(1, 1500) g")
    settings = DataSettings(
        mcp_service_token=SecretStr("t" * 32),  # type: ignore[call-arg]
        readonly_database_url=SecretStr(database.url("readonly", driver=False)),
    )
    yield build_server(settings)
    with psycopg.connect(database.url("migrator", driver=False), autocommit=True) as conn:
        conn.execute("DROP TABLE demo_data.latency")


async def test_describe_table_lists_columns(data_server) -> None:
    result = await call(data_server, "describe_table", {"table": "latency"})
    assert (result.structuredContent or {})["columns"] == [
        {"name": "region", "type": "text", "nullable": True},
        {"name": "ms", "type": "integer", "nullable": True},
    ]


async def test_describe_unknown_table(data_server) -> None:
    assert (
        error_of(await call(data_server, "describe_table", {"table": "tasks"}))["error_code"]
        == "table_not_found"
    )


async def test_sql_query_forces_limit_and_reports_truncation(data_server) -> None:
    result = await call(data_server, "sql_query", {"sql": "SELECT ms FROM latency ORDER BY ms"})
    data = result.structuredContent or {}
    assert data["columns"] == ["ms"]
    assert len(data["rows"]) == 1000
    assert data["truncated"] is True


async def test_sql_query_cannot_read_app_tables(data_server) -> None:
    err = error_of(await call(data_server, "sql_query", {"sql": "SELECT * FROM public.tasks"}))
    assert err["error_code"] == "sql_error"
    assert "permission denied" in err["message"]


async def test_sql_query_rejects_writes_before_touching_the_db(data_server) -> None:
    err = error_of(await call(data_server, "sql_query", {"sql": "DELETE FROM latency"}))
    assert err["error_code"] == "sql_rejected"


async def test_sql_query_statement_timeout(data_server) -> None:
    err = error_of(await call(data_server, "sql_query", {"sql": "SELECT pg_sleep(11)"}))
    assert err["error_code"] == "timeout"
