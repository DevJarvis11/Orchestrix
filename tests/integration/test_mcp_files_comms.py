"""mcp-files and mcp-comms against Postgres (SPEC 12.3). Headers are injected per test."""

import base64
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from orchestrix_core.config import Settings
from orchestrix_core.db.models import Artifact, EmailOutbox
from orchestrix_core.db.repos.tasks import TaskRepo, UserRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker
from orchestrix_core.schemas import Budget, TaskCreate
from orchestrix_mcp_comms.server import CommsSettings
from orchestrix_mcp_comms.server import build_server as build_comms
from orchestrix_mcp_files.server import FilesSettings
from orchestrix_mcp_files.server import build_server as build_files
from orchestrix_tools import conventions
from tests.unit.mcp.conftest import call, error_of

pytestmark = pytest.mark.integration

TOKEN = SecretStr("t" * 32)


@dataclass
class FakeObjects:
    stored: dict[str, bytes] = field(default_factory=dict)

    async def get(self, key: str) -> bytes:
        return self.stored[key]

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        self.stored[key] = body

    async def delete(self, key: str) -> None:
        self.stored.pop(key, None)


@pytest.fixture
def headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values: dict[str, str] = {}
    monkeypatch.setattr(conventions, "request_header", lambda _ctx, name: values.get(name))
    return values


@pytest.fixture
async def env(settings: Settings) -> Any:
    engine = create_engine(settings)
    sessions = create_sessionmaker(engine)
    email = f"{uuid.uuid4().hex[:8]}@test.local"
    users = UserRepo(sessions)
    user = await users.upsert(f"dev|{email}", email, email)
    team = await users.ensure_team_member(user.id, "mcp-tests")

    async def new_run() -> tuple[uuid.UUID, uuid.UUID]:
        created = await TaskRepo(sessions).create(
            requester_id=user.id,
            team_id=team.id,
            body=TaskCreate(title="t", description="d"),
            budget=Budget(),
            idempotency_key=None,
            graph_version="test",
        )
        return created.task_id, created.run_id

    yield sessions, new_run
    await engine.dispose()


def files_server(settings: Settings, sessions: Any, objects: FakeObjects) -> Any:
    cfg = FilesSettings(mcp_service_token=TOKEN, database_url=settings.database_url)  # type: ignore[call-arg]
    return build_files(cfg, objects, sessions)  # type: ignore[arg-type]


async def test_write_then_read_artifact_in_the_same_run(
    settings: Settings, env: Any, headers: dict[str, str]
) -> None:
    sessions, new_run = env
    _, run_id = await new_run()
    objects = FakeObjects()
    server = files_server(settings, sessions, objects)
    headers |= {"x-run-id": str(run_id), "x-idempotency-key": "k-1"}
    content = base64.b64encode(b"region,ms\neu,12\n").decode()

    written = await call(
        server, "write_artifact", {"name": "lat.csv", "mime": "text/csv", "content_b64": content}
    )
    artifact_id = (written.structuredContent or {})["artifact_id"]
    again = await call(
        server, "write_artifact", {"name": "lat.csv", "mime": "text/csv", "content_b64": content}
    )
    assert (again.structuredContent or {})["artifact_id"] == artifact_id  # same idempotency key

    read = await call(server, "read_artifact", {"artifact_id": artifact_id})
    assert (read.structuredContent or {})["text"] == "region,ms\neu,12\n"
    async with sessions() as s:
        rows = (await s.execute(select(Artifact).where(Artifact.run_id == run_id))).scalars().all()
    assert len(rows) == 1


async def test_cannot_read_another_runs_artifact(
    settings: Settings, env: Any, headers: dict[str, str]
) -> None:
    sessions, new_run = env
    _, run_a = await new_run()
    _, run_b = await new_run()
    objects = FakeObjects()
    server = files_server(settings, sessions, objects)
    headers |= {"x-run-id": str(run_a), "x-idempotency-key": "k-a"}
    written = await call(
        server,
        "write_artifact",
        {
            "name": "secret.md",
            "mime": "text/markdown",
            "content_b64": base64.b64encode(b"x").decode(),
        },
    )
    headers["x-run-id"] = str(run_b)
    err = error_of(
        await call(
            server,
            "read_artifact",
            {"artifact_id": (written.structuredContent or {})["artifact_id"]},
        )
    )
    assert err["error_code"] == "artifact_not_found"


async def test_task_attachments_are_readable_by_its_run(
    settings: Settings, env: Any, headers: dict[str, str]
) -> None:
    sessions, new_run = env
    task_id, run_id = await new_run()
    attachment = uuid.uuid4()
    async with sessions.begin() as s:
        s.add(
            Artifact(
                id=attachment,
                task_id=task_id,
                name="notes.md",
                mime="text/markdown",
                bytes=5,
                object_key=f"artifacts/{attachment}",
                sha256="x",
            )
        )
    objects = FakeObjects({f"artifacts/{attachment}": b"hello"})
    headers["x-run-id"] = str(run_id)
    result = await call(
        files_server(settings, sessions, objects),
        "read_artifact",
        {"artifact_id": str(attachment), "max_chars": 100},
    )
    assert (result.structuredContent or {})["text"] == "hello"


async def test_write_artifact_validates_input(
    settings: Settings, env: Any, headers: dict[str, str]
) -> None:
    sessions, new_run = env
    _, run_id = await new_run()
    headers["x-run-id"] = str(run_id)
    server = files_server(settings, sessions, FakeObjects())
    err = error_of(
        await call(
            server, "write_artifact", {"name": "x.txt", "mime": "text/plain", "content_b64": "%%%"}
        )
    )
    assert err["error_code"] == "bad_content"
    assert (
        await call(
            server,
            "write_artifact",
            {"name": "../etc/passwd", "mime": "text/plain", "content_b64": "eA=="},
        )
    ).isError


async def test_send_email_is_simulated_and_idempotent(
    settings: Settings, env: Any, headers: dict[str, str]
) -> None:
    sessions, new_run = env
    _, run_id = await new_run()
    server = build_comms(
        CommsSettings(mcp_service_token=TOKEN, database_url=settings.database_url), sessions
    )  # type: ignore[call-arg]
    headers |= {"x-run-id": str(run_id), "x-idempotency-key": "email-1"}
    args = {"to": ["platform@example.com"], "subject": "VDB recommendation", "body_md": "# Summary"}
    first = await call(server, "send_email", args)
    second = await call(server, "send_email", args)
    assert (first.structuredContent or {})["status"] == "simulated"
    assert first.structuredContent == second.structuredContent
    async with sessions() as s:
        outbox = (
            (await s.execute(select(EmailOutbox).where(EmailOutbox.run_id == run_id)))
            .scalars()
            .all()
        )
    assert [(o.to_addrs, o.status) for o in outbox] == [(["platform@example.com"], "simulated")]


async def test_send_email_validates_recipients(
    settings: Settings, env: Any, headers: dict[str, str]
) -> None:
    sessions, new_run = env
    _, run_id = await new_run()
    headers["x-run-id"] = str(run_id)
    server = build_comms(
        CommsSettings(mcp_service_token=TOKEN, database_url=settings.database_url), sessions
    )  # type: ignore[call-arg]
    assert (
        await call(server, "send_email", {"to": ["not-an-email"], "subject": "s", "body_md": "b"})
    ).isError
    eleven = [f"u{i}@example.com" for i in range(11)]
    assert (
        await call(server, "send_email", {"to": eleven, "subject": "s", "body_md": "b"})
    ).isError
