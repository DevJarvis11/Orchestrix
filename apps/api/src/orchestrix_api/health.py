"""Liveness and readiness (SPEC 17.1): /healthz is process-only, /readyz checks dependencies."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Check = Callable[[], Awaitable[None]]
"""A readiness probe. Returns on success, raises on failure."""

CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter(tags=["health"])


class CheckResult(BaseModel):
    status: Literal["ok", "fail"]
    error: str | None = None


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]


def database_check(engine: AsyncEngine) -> Check:
    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return check


def redis_check(redis: Redis) -> Check:
    async def check() -> None:
        await redis.ping()  # pyright: ignore[reportUnknownMemberType]

    return check


def http_check(client: httpx.AsyncClient, url: str) -> Check:
    async def check() -> None:
        response = await client.get(url)
        response.raise_for_status()

    return check


async def _run(check: Check) -> CheckResult:
    try:
        await asyncio.wait_for(check(), CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return CheckResult(status="fail", error="timeout")
    except Exception as exc:
        return CheckResult(status="fail", error=f"{type(exc).__name__}: {exc}")
    return CheckResult(status="ok")


async def run_checks(checks: Mapping[str, Check]) -> Readiness:
    names = list(checks)
    results = await asyncio.gather(*(_run(checks[name]) for name in names))
    by_name = dict(zip(names, results, strict=True))
    ready = all(r.status == "ok" for r in results)
    return Readiness(status="ready" if ready else "not_ready", checks=by_name)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=Readiness, responses={503: {"model": Readiness}})
async def readyz(request: Request) -> JSONResponse:
    checks: Mapping[str, Check] = request.app.state.readiness_checks
    readiness = await run_checks(checks)
    code = 200 if readiness.status == "ready" else 503
    return JSONResponse(readiness.model_dump(), status_code=code)
