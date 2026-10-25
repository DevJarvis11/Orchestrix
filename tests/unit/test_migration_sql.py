"""The initial migration renders every modelled table and the SPEC 10 data rules (offline SQL)."""

import io

import pytest
from alembic import command
from alembic.config import Config

from orchestrix_core.db.models import TABLES_WITH_UPDATED_AT, Base


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    buffer = io.StringIO()
    cfg = Config("alembic.ini", output_buffer=buffer)
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://u:p@localhost/db")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head", sql=True)
    return buffer.getvalue()


def test_every_model_table_is_created(upgrade_sql: str) -> None:
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} (" in upgrade_sql, table


def test_spec_indexes(upgrade_sql: str) -> None:
    assert (
        "CREATE INDEX memories_embedding_hnsw ON memories USING hnsw (embedding halfvec_cosine_ops)"
        in upgrade_sql
    )
    assert "CREATE INDEX memories_tsv_gin ON memories USING gin (tsv)" in upgrade_sql
    assert "WHERE consolidated_into IS NULL" in upgrade_sql
    assert "CREATE INDEX runs_lease_idx ON runs (status, lease_expires_at)" in upgrade_sql


def test_uuidv7_and_generated_columns(upgrade_sql: str) -> None:
    assert upgrade_sql.count("DEFAULT uuidv7()") >= 15
    assert "embedding HALFVEC(2560)," in upgrade_sql
    assert "GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED" in upgrade_sql


def test_updated_at_triggers(upgrade_sql: str) -> None:
    for table in TABLES_WITH_UPDATED_AT:
        assert f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}" in upgrade_sql


def test_role_rules(upgrade_sql: str) -> None:
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM orchestrix_app" in upgrade_sql
    assert "CREATE SCHEMA demo_data" in upgrade_sql
    assert "GRANT USAGE ON SCHEMA demo_data TO orchestrix_readonly" in upgrade_sql
