"""M2 Done-when: a task submitted through the API runs end to end on fake models and its events
stream over SSE (FR-03, FR-06). Runs in `make test-resilience` (Compose stack, fake models)."""

import json
import subprocess

import httpx
import pytest

pytestmark = pytest.mark.resilience

API = "http://127.0.0.1:58100"


def test_fr03_fr06_task_runs_end_to_end_and_streams_events() -> None:
    token = subprocess.run(
        ["make", "--no-print-directory", "dev-token"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[-1]
    headers = {"Authorization": f"Bearer {token}"}
    created = (
        httpx.post(
            f"{API}/v1/tasks",
            json={"title": "Stream", "description": "Stream my run."},
            headers=headers,
        )
        .raise_for_status()
        .json()
    )

    events: list[tuple[int, str, dict[str, object]]] = []
    with httpx.stream(
        "GET", f"{API}/v1/runs/{created['run_id']}/events", headers=headers, timeout=120
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        block: dict[str, str] = {}
        for line in response.iter_lines():
            if line == "":
                if "event" in block:
                    events.append((int(block["id"]), block["event"], json.loads(block["data"])))
                block = {}
            elif not line.startswith(":") and ": " in line:
                key, value = line.split(": ", 1)
                block[key] = value

    types = [t for _, t, _ in events]
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    for expected in ("plan.created", "subtask.started", "review.completed", "deliverable.ready"):
        assert expected in types
    assert types.count("subtask.started") == 3
    ids = [i for i, _, _ in events]
    assert ids == sorted(set(ids))

    detail = httpx.get(f"{API}/v1/tasks/{created['task_id']}", headers=headers).json()
    assert detail["status"] == "completed"
    assert [s["status"] for s in detail["subtasks"]] == ["approved", "approved", "approved"]
    assert detail["deliverable_artifact_id"]
