"""A pgvector PG18 container initialised by the same initdb script as Compose, then migrated."""

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer

from orchestrix_core.db.migrate import migrate

ROOT = Path(__file__).resolve().parents[2]
INITDB = ROOT / "infra" / "compose" / "postgres" / "initdb"
PG_IMAGE = "pgvector/pgvector:pg18"
PASSWORDS = {
    "ORCHESTRIX_APP_PASSWORD": "app-test-pw",
    "ORCHESTRIX_MIGRATOR_PASSWORD": "migrator-test-pw",
    "ORCHESTRIX_READONLY_PASSWORD": "readonly-test-pw",
    "LITELLM_DB_PASSWORD": "litellm-test-pw",
}


@dataclass(frozen=True)
class Database:
    host: str
    port: int

    def url(self, role: str, *, driver: bool = True) -> str:
        password = PASSWORDS[f"ORCHESTRIX_{role.upper()}_PASSWORD"]
        scheme = "postgresql+psycopg" if driver else "postgresql"
        return f"{scheme}://orchestrix_{role}:{password}@{self.host}:{self.port}/orchestrix"


@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer]:
    container = PostgresContainer(
        PG_IMAGE, username="postgres", password="super-test-pw", dbname="postgres"
    )
    container.with_volume_mapping(INITDB, "/docker-entrypoint-initdb.d", mode="ro")
    for key, value in PASSWORDS.items():
        container.with_env(key, value)
    with container:
        yield container


@pytest.fixture(scope="session")
async def database(pg_container: PostgresContainer) -> Database:
    db = Database(pg_container.get_container_host_ip(), int(pg_container.get_exposed_port(5432)))
    await migrate(db.url("migrator"), ROOT / "alembic.ini")
    return db
