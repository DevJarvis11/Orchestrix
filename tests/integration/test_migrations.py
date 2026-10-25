"""Migrations against a real PG18 + pgvector (SPEC 10, plan M1 'make migrate creates every table')."""

import asyncio

import psycopg
import pytest
from alembic import command
from psycopg import errors

from orchestrix_core.db.migrate import alembic_config, migrate
from orchestrix_core.db.models import Base
from tests.integration.conftest import ROOT, Database

pytestmark = pytest.mark.integration

CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def connect(db: Database, role: str) -> psycopg.Connection:
    return psycopg.connect(db.url(role, driver=False), autocommit=True)


def public_tables(db: Database) -> set[str]:
    with connect(db, "app") as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def test_every_table_created(database: Database) -> None:
    tables = public_tables(database)
    assert set(Base.metadata.tables) <= tables
    assert tables >= CHECKPOINT_TABLES
    assert "alembic_version" in tables


def test_models_and_database_have_no_drift(database: Database) -> None:
    command.check(alembic_config(database.url("migrator"), ROOT / "alembic.ini"))


def test_vector_extension_and_hnsw_index(database: Database) -> None:
    with connect(database, "app") as conn:
        assert conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'memories_embedding_hnsw'"
        ).fetchone()
    assert index is not None
    assert "hnsw" in index[0]
    assert "halfvec_cosine_ops" in index[0]


def test_uuidv7_defaults_and_updated_at_trigger(database: Database) -> None:
    with connect(database, "app") as conn:
        team = conn.execute(
            "INSERT INTO teams (name) VALUES ('trigger-team') RETURNING id"
        ).fetchone()
        user = conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name) "
            "VALUES ('sub-trigger', 'trigger@example.com', 'T') RETURNING id"
        ).fetchone()
        assert team is not None
        assert user is not None
        assert team[0].version == 7
        row = conn.execute(
            "INSERT INTO tasks (team_id, requester_id, title, description, status, budget) "
            "VALUES (%s, %s, 't', 'd', 'queued', '{}') RETURNING id, updated_at",
            (team[0], user[0]),
        ).fetchone()
        assert row is not None
        task_id, created = row
        updated = conn.execute(
            "UPDATE tasks SET status = 'planning' WHERE id = %s RETURNING updated_at", (task_id,)
        ).fetchone()
        assert updated is not None
        assert updated[0] > created
        overrides = conn.execute(
            "SELECT model_overrides FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        assert overrides == ({},)


def test_task_description_limit(database: Database) -> None:
    with connect(database, "app") as conn, pytest.raises(errors.CheckViolation):
        conn.execute(
            "INSERT INTO tasks (team_id, requester_id, title, description, status, budget) "
            "SELECT t.id, u.id, 't', repeat('x', 8001), 'queued', '{}' "
            "FROM teams t, users u LIMIT 1"
        )


def test_audit_log_is_append_only_for_app(database: Database) -> None:
    with connect(database, "app") as conn:
        conn.execute(
            "INSERT INTO audit_log (action, target_type, target_id) VALUES ('test', 'task', 'x')"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("UPDATE audit_log SET reason = 'tamper'")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_log")


def test_app_can_use_checkpointer_tables(database: Database) -> None:
    with connect(database, "app") as conn:
        conn.execute("SELECT count(*) FROM checkpoints").fetchone()


def test_readonly_role_sees_only_demo_data(database: Database) -> None:
    with connect(database, "migrator") as conn:
        conn.execute("CREATE TABLE demo_data.probe (n int)")
        conn.execute("INSERT INTO demo_data.probe VALUES (1)")
    try:
        with connect(database, "readonly") as conn:
            assert conn.execute("SELECT n FROM demo_data.probe").fetchone() == (1,)
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM tasks")
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("INSERT INTO demo_data.probe VALUES (2)")
    finally:
        with connect(database, "migrator") as conn:
            conn.execute("DROP TABLE demo_data.probe")


def test_app_role_cannot_run_ddl(database: Database) -> None:
    with connect(database, "app") as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute("CREATE TABLE sneaky (n int)")


async def test_downgrade_then_upgrade_is_clean(database: Database) -> None:
    cfg = alembic_config(database.url("migrator"), ROOT / "alembic.ini")
    # env.py calls asyncio.run(), so Alembic needs a thread without a running loop.
    await asyncio.to_thread(command.downgrade, cfg, "base")
    remaining = public_tables(database) - CHECKPOINT_TABLES - {"alembic_version"}
    assert remaining == set()
    await migrate(database.url("migrator"), ROOT / "alembic.ini")
    assert set(Base.metadata.tables) <= public_tables(database)
