"""A pgvector PG18 container initialised by the same initdb script as Compose, then migrated."""

import secrets
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from orchestrix_core.config import Settings
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


DEV_SECRET = secrets.token_hex(32)  # per test session; never a literal in the repo


@pytest.fixture(scope="session")
def redis_url() -> Generator[str]:
    with RedisContainer("redis:8.10.2") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def settings(database: Database, redis_url: str) -> Settings:
    return Settings(  # pyright: ignore[reportCallIssue]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        env="development",
        auth_mode="dev",
        dev_jwt_secret=SecretStr(DEV_SECRET),
        database_url=SecretStr(database.url("app")),
        migrator_database_url=SecretStr(database.url("migrator")),
        redis_url=redis_url,
        fake_models=True,
        litellm_url="http://litellm.invalid",
        opa_url="http://opa.invalid",
    )


@dataclass
class MemoryStore:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = body


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
async def worker_deps(settings: Settings, store: MemoryStore) -> AsyncGenerator[object]:
    from orchestrix_worker.runner import build_deps, close_deps

    deps = await build_deps(settings, store=store)
    yield deps
    await close_deps(deps)


def token_for(email: str, roles: list[str] | None = None) -> str:
    from orchestrix_api.security.dev_jwt import issue

    return issue(
        DEV_SECRET, sub=f"dev|{email}", email=email, name=email, roles=roles or ["requester"]
    )
