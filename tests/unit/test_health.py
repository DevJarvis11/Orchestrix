"""/healthz and /readyz (SPEC 17.1): readiness checks DB, Redis, OPA and the gateway."""

import asyncio
from collections.abc import Mapping

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from orchestrix_api.health import Check, http_check, run_checks
from orchestrix_api.main import create_app


async def ok() -> None:
    return None


async def boom() -> None:
    raise ConnectionError("refused")


async def hang() -> None:
    await asyncio.sleep(10)


def client(checks: Mapping[str, Check]) -> TestClient:
    return TestClient(create_app(readiness_checks=checks))


def test_healthz_needs_no_dependencies() -> None:
    with client({"database": boom}) as c:
        response = c.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ready_when_all_checks_pass() -> None:
    checks = {"database": ok, "redis": ok, "opa": ok, "litellm": ok}
    with client(checks) as c:
        response = c.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"database", "redis", "opa", "litellm"}


def test_readyz_503_names_the_failing_dependency() -> None:
    with client({"database": ok, "redis": boom}) as c:
        response = c.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == {"status": "ok", "error": None}
    assert body["checks"]["redis"]["status"] == "fail"
    assert "refused" in body["checks"]["redis"]["error"]


async def test_slow_check_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrix_api.health.CHECK_TIMEOUT_SECONDS", 0.05)
    readiness = await run_checks({"opa": hang})
    assert readiness.checks["opa"].error == "timeout"


@respx.mock
async def test_http_check_against_opa_and_litellm() -> None:
    respx.get("http://opa:8181/health").respond(200)
    respx.get("http://litellm:4000/health/liveliness").respond(500)
    async with httpx.AsyncClient() as http:
        readiness = await run_checks(
            {
                "opa": http_check(http, "http://opa:8181/health"),
                "litellm": http_check(http, "http://litellm:4000/health/liveliness"),
            }
        )
    assert readiness.checks["opa"].status == "ok"
    assert readiness.checks["litellm"].status == "fail"
    assert readiness.status == "not_ready"
