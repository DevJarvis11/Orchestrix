"""NFR-01: SIGKILL the worker holding a run mid-run; another worker completes it from the last
checkpoint with no duplicated side effects. Run with `make test-resilience` (Compose, fake models,
two workers, 10 s leases)."""

import subprocess
import time
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest

from orchestrix_core.config import get_settings

pytestmark = pytest.mark.resilience

API = "http://127.0.0.1:58100"
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "infra/compose/docker-compose.yml"]


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def query(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(get_settings().checkpointer_dsn) as conn:
        return conn.execute(sql, params).fetchall()  # pyright: ignore[reportArgumentType]


def wait_for(predicate: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def container_for(lease_owner: str) -> str:
    """lease_owner is '<container hostname>:<pid>'; the hostname is the short container id."""
    hostname = lease_owner.split(":", 1)[0]
    for cid in sh(*COMPOSE, "--profile", "core", "ps", "-q", "worker").splitlines():
        if sh("docker", "inspect", "-f", "{{.Config.Hostname}}", cid) == hostname:
            return cid
    raise AssertionError(f"no worker container with hostname {hostname}")


def test_nfr01_sigkill_worker_mid_run_completes_without_duplicates() -> None:
    token = sh("make", "--no-print-directory", "dev-token").splitlines()[-1]
    headers = {"Authorization": f"Bearer {token}"}
    created = (
        httpx.post(
            f"{API}/v1/tasks",
            json={"title": "Resilience", "description": "Survive a crash."},
            headers=headers,
        )
        .raise_for_status()
        .json()
    )
    run_id = created["run_id"]

    def tool_calls() -> int:
        return query("SELECT count(*) FROM tool_calls WHERE run_id = %s", run_id)[0][0]

    # s1 and s2 run in parallel; once both side effects exist, s3 is in flight. Kill now.
    wait_for(lambda: tool_calls() >= 2, 90, "s1 and s2 to finish their work")
    [(owner,)] = query("SELECT lease_owner FROM runs WHERE id = %s", run_id)
    assert owner, "the run should be leased while it executes"
    sh("docker", "kill", "--signal", "KILL", container_for(owner))

    def status() -> str:
        return httpx.get(f"{API}/v1/tasks/{created['task_id']}", headers=headers).json()["status"]

    wait_for(lambda: status() == "completed", 150, "another worker to complete the run")

    [(reclaims, final_owner)] = query(
        "SELECT reclaim_count, lease_owner FROM runs WHERE id = %s", run_id
    )
    assert reclaims >= 1
    assert final_owner is None
    assert tool_calls() == 3  # one side effect per subtask: nothing repeated after the crash
    statuses = [s for (s,) in query("SELECT status FROM subtasks WHERE run_id = %s", run_id)]
    assert sorted(statuses) == ["approved", "approved", "approved"]
    resumed = query(
        "SELECT data->>'reason' FROM run_events WHERE run_id = %s AND type = 'run.resumed'", run_id
    )
    assert ("reclaimed",) in resumed
