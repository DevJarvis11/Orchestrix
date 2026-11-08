"""A pgvector PG18 container initialised by the same initdb script as Compose, then migrated."""

import secrets
import time
import uuid
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer

from orchestrix_core.config import Settings
from orchestrix_core.db.migrate import migrate

ROOT = Path(__file__).resolve().parents[2]
INITDB = ROOT / "infra" / "compose" / "postgres" / "initdb"
PG_IMAGE = "pgvector/pgvector:pg18"
OPA_IMAGE = "openpolicyagent/opa:1.21.3"  # same as Compose
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

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


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


@pytest.fixture(scope="session")
def opa_url() -> Generator[str]:
    """The real OPA with the repository's policies (SPEC 15.1), as Compose runs it."""
    container = (
        DockerContainer(OPA_IMAGE)
        .with_volume_mapping(str(ROOT / "policies"), "/policies", mode="ro")
        .with_command("run --server --addr=0.0.0.0:8181 /policies")
        .with_exposed_ports(8181)
    )
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8181)}"
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        yield url


@pytest.fixture(scope="session")
def api_settings(settings: Settings, opa_url: str) -> Settings:
    return settings.model_copy(update={"opa_url": opa_url})


async def _ok() -> None:
    return None


@pytest.fixture
async def client(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient]:
    """The API on real Postgres, Redis and OPA; jobs are captured instead of enqueued."""
    from orchestrix_api.main import create_app

    enqueued: list[tuple[str, str]] = []

    async def fake_enqueue(name: str, run_id: str) -> None:
        enqueued.append((name, run_id))

    for module in ("tasks", "escalations", "replay"):
        monkeypatch.setattr(f"orchestrix_api.routers.{module}.enqueue", fake_enqueue)
    app = create_app(api_settings, readiness_checks={"database": _ok})
    async with LifespanManager(app):
        app.state.resources.objects = MemoryStore()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
            c.enqueued = enqueued  # type: ignore[attr-defined]
            yield c


def auth(email: str | None = None, roles: list[str] | None = None) -> dict[str, str]:
    email = email or f"{uuid.uuid4().hex[:8]}@test.local"
    return {"Authorization": f"Bearer {token_for(email, roles)}"}


async def signed_in(
    client: httpx.AsyncClient, *, team: str | None = "api-tests", roles: list[str] | None = None
) -> dict[str, str]:
    """A fresh user (what `make dev-token` does), optionally in a team; returns auth headers."""
    from orchestrix_core.db.repos.tasks import UserRepo

    email = f"{uuid.uuid4().hex[:8]}@test.local"
    users = UserRepo(client._transport.app.state.resources.sessions)  # type: ignore[attr-defined]
    user = await users.upsert(f"dev|{email}", email, email)
    if team is not None:
        await users.ensure_team_member(user.id, team)
    return auth(email, roles)
