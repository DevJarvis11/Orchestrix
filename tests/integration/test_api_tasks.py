"""Tasks API and SSE against real Postgres and Redis (SPEC 17)."""

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

from tests.integration.conftest import signed_in

pytestmark = pytest.mark.integration


TASK = {"title": "Latency report", "description": "Summarise p95 latency."}


async def test_fr03_create_returns_202_and_enqueues(client: httpx.AsyncClient) -> None:
    headers = await signed_in(client)
    r = await client.post("/v1/tasks", json=TASK, headers=headers)
    assert r.status_code == 202
    body = r.json()
    assert set(body) == {"task_id", "run_id"}
    assert client.enqueued == [("orchestrix.start_run", body["run_id"])]  # type: ignore[attr-defined]
    assert r.headers["X-Request-Id"]

    detail = (await client.get(f"/v1/tasks/{body['task_id']}", headers=headers)).json()
    assert detail["status"] == "queued"
    assert detail["run_id"] == body["run_id"]


async def test_fr05_same_idempotency_key_returns_original_task(client: httpx.AsyncClient) -> None:
    headers = await signed_in(client)
    first = await client.post(
        "/v1/tasks", json=TASK, headers=headers | {"Idempotency-Key": "abc-1"}
    )
    second = await client.post(
        "/v1/tasks", json=TASK, headers=headers | {"Idempotency-Key": "abc-1"}
    )
    other = await client.post(
        "/v1/tasks", json=TASK, headers=headers | {"Idempotency-Key": "abc-2"}
    )
    assert first.json() == second.json()
    assert other.json()["task_id"] != first.json()["task_id"]
    assert len(client.enqueued) == 2  # type: ignore[attr-defined]


async def test_fr02_validation_errors_are_problem_json(client: httpx.AsyncClient) -> None:
    headers = await signed_in(client)
    r = await client.post(
        "/v1/tasks", json={"title": "x", "description": "d" * 8001}, headers=headers
    )
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["errors"][0]["loc"][-1] == "description"


async def test_auth_required_and_tasks_are_private(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/tasks", json=TASK)).status_code == 401
    owner = await signed_in(client)
    task_id = (await client.post("/v1/tasks", json=TASK, headers=owner)).json()["task_id"]
    teammate = await signed_in(client)  # same team, requester only: may not (SPEC 2)
    assert (await client.get(f"/v1/tasks/{task_id}", headers=teammate)).status_code == 404
    reviewer = await signed_in(client, roles=["requester", "reviewer"])  # team reviewer: may
    assert (await client.get(f"/v1/tasks/{task_id}", headers=reviewer)).status_code == 200
    stranger = await signed_in(client, team="another-team")
    assert (await client.get(f"/v1/tasks/{task_id}", headers=stranger)).status_code == 404
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert (await client.get(f"/v1/tasks/{task_id}", headers=bad)).status_code == 401


async def test_fr02_user_without_team_is_forbidden(client: httpx.AsyncClient) -> None:
    headers = await signed_in(client, team=None)
    assert (await client.post("/v1/tasks", json=TASK, headers=headers)).status_code == 403


async def test_fr04_cancel_queued_task_now_and_reject_second_cancel(
    client: httpx.AsyncClient,
) -> None:
    headers = await signed_in(client)
    created = (await client.post("/v1/tasks", json=TASK, headers=headers)).json()
    r = await client.post(f"/v1/tasks/{created['task_id']}/cancel", headers=headers)
    assert r.status_code == 202
    assert r.json()["status"] == "cancelled"
    assert (
        await client.post(f"/v1/tasks/{created['task_id']}/cancel", headers=headers)
    ).status_code == 409


def parse_sse(raw: str) -> list[dict[str, Any]]:
    events = []
    for block in raw.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if "event" in fields:
            events.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return events


async def test_fr06_sse_replays_then_streams_live_and_dedupes(client: httpx.AsyncClient) -> None:
    headers = await signed_in(client)
    run_id = (await client.post("/v1/tasks", json=TASK, headers=headers)).json()["run_id"]
    res = client._transport.app.state.resources  # type: ignore[attr-defined]
    run_uuid = uuid.UUID(run_id)
    first = await res.events.emit(run_uuid, {"type": "run.started", "run_id": run_id})
    await res.events.emit(run_uuid, {"type": "plan.created", "run_id": run_id, "version": 1})

    async def live() -> None:
        await asyncio.sleep(0.5)  # the stream is subscribed and has replayed by now
        await res.events.emit(
            run_uuid, {"type": "subtask.started", "run_id": run_id, "subtask": "s1"}
        )
        await res.events.emit(run_uuid, {"type": "run.completed", "run_id": run_id})

    producer = asyncio.create_task(live())
    r = await client.get(
        f"/v1/runs/{run_id}/events", headers=headers | {"Last-Event-ID": str(first)}
    )
    await producer
    events = parse_sse(r.text)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert [e["event"] for e in events] == ["plan.created", "subtask.started", "run.completed"]
    ids = [e["id"] for e in events]
    assert ids == sorted(set(ids))  # no duplicates, in order
    assert all(e["data"]["run_id"] == run_id for e in events)


async def upload(
    client: httpx.AsyncClient, headers: dict[str, str], name: str, body: bytes
) -> httpx.Response:
    return await client.post(
        "/v1/uploads", files={"file": (name, body, "application/octet-stream")}, headers=headers
    )


async def test_fr02_upload_sniffs_type_and_attaches_to_task(client: httpx.AsyncClient) -> None:
    headers = await signed_in(client)
    r = await upload(client, headers, "latency.csv", b"region,ms\neu,12\n")
    assert r.status_code == 201
    assert r.json()["mime"] == "text/csv"
    artifact_id = r.json()["artifact_id"]

    created = await client.post(
        "/v1/tasks", json=TASK | {"attachments": [artifact_id]}, headers=headers
    )
    assert created.status_code == 202
    again = await client.post(
        "/v1/tasks", json=TASK | {"attachments": [artifact_id]}, headers=headers
    )
    assert again.status_code == 422  # already attached


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("evil.pdf", b"MZ\x90\x00 not a pdf"),
        ("photo.png", b"\x89PNG\r\n"),
        ("notes.txt", b"\xff\xfe\x00bad"),
    ],
)
async def test_fr02_upload_rejects_unsupported_content(
    client: httpx.AsyncClient, name: str, body: bytes
) -> None:
    r = await upload(client, await signed_in(client), name, body)
    assert r.status_code == 415
    assert r.headers["content-type"] == "application/problem+json"


async def test_fr02_upload_size_limit(client: httpx.AsyncClient) -> None:
    r = await upload(client, await signed_in(client), "big.txt", b"a" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 413


async def test_fr02_cannot_attach_someone_elses_upload(client: httpx.AsyncClient) -> None:
    owner = await signed_in(client)
    artifact_id = (await upload(client, owner, "x.md", b"# hi")).json()["artifact_id"]
    thief = await signed_in(client)
    r = await client.post("/v1/tasks", json=TASK | {"attachments": [artifact_id]}, headers=thief)
    assert r.status_code == 422
