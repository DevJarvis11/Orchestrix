"""App factory and lifespan (SPEC 9)."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from orchestrix_agents.graph import build_graph
from orchestrix_agents.serde import make_serializer
from orchestrix_api import errors
from orchestrix_api.deps import AppResources
from orchestrix_api.health import Check, database_check, http_check, redis_check
from orchestrix_api.health import router as health_router
from orchestrix_api.middleware import RequestIdMiddleware
from orchestrix_api.routers import (
    admin,
    auth,
    costs,
    escalations,
    memories,
    models,
    observe,
    replay,
    runs,
    tasks,
    uploads,
)
from orchestrix_api.security import headers as security_headers
from orchestrix_core.config import Settings, get_settings
from orchestrix_core.db.repos.audit import AuditRepo
from orchestrix_core.db.repos.escalations import EscalationRepo
from orchestrix_core.db.repos.events import EventRepo
from orchestrix_core.db.repos.runs import RunRepo
from orchestrix_core.db.repos.tasks import TaskRepo, UserRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker
from orchestrix_core.events import EventBus
from orchestrix_core.logging import configure_logging
from orchestrix_core.schemas.events import run_event_adapter
from orchestrix_core.storage import S3ObjectStore
from orchestrix_core.telemetry import init_tracing
from orchestrix_policy.client import PolicyClient


def create_app(
    settings: Settings | None = None, readiness_checks: Mapping[str, Check] | None = None
) -> FastAPI:
    """Build the API. Tests pass `readiness_checks` alone to run health routes without resources."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if readiness_checks is not None and settings is None:
            app.state.readiness_checks = readiness_checks
            yield
            return
        cfg = settings or get_settings()
        init_tracing("orchestrix-api", cfg)
        engine = create_engine(cfg)
        sessions = create_sessionmaker(engine)
        redis = Redis.from_url(cfg.redis_url)  # pyright: ignore[reportUnknownMemberType]
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            cfg.checkpointer_dsn,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            max_size=4,
            open=False,
        )
        await pool.open()
        graph = build_graph(AsyncPostgresSaver(pool, serde=make_serializer()))  # pyright: ignore[reportArgumentType]
        async with httpx.AsyncClient(timeout=5.0) as http:
            event_log = EventRepo(sessions)
            app.state.resources = AppResources(
                settings=cfg,
                sessions=sessions,
                redis=redis,
                http=http,
                tasks=TaskRepo(sessions),
                runs=RunRepo(sessions),
                users=UserRepo(sessions),
                event_log=event_log,
                events=EventBus(event_log, redis),
                objects=S3ObjectStore.from_settings(cfg),
                escalations=EscalationRepo(sessions),
                audit=AuditRepo(sessions),
                policy=PolicyClient(cfg.opa_url, http),
                graph=graph,
            )
            app.state.readiness_checks = readiness_checks or {
                "database": database_check(engine),
                "redis": redis_check(redis),
                "opa": http_check(http, f"{cfg.opa_url}/health"),
                "litellm": http_check(http, f"{cfg.litellm_url}/health/liveliness"),
            }
            try:
                yield
            finally:
                await redis.aclose()
                await pool.close()
                await engine.dispose()

    configure_logging()
    app = FastAPI(title="Orchestrix API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    # Health-only test apps pass readiness_checks alone and have no settings to read.
    origins = settings.cors_origins if settings else []
    if settings is None and readiness_checks is None:
        origins = get_settings().cors_origins
    security_headers.install(app, origins)
    errors.install(app)
    app.include_router(health_router)
    app.include_router(tasks.router)
    app.include_router(runs.router)
    app.include_router(models.router)
    app.include_router(uploads.router)
    app.include_router(escalations.router)
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(observe.router)
    app.include_router(costs.router)
    app.include_router(memories.router)
    app.include_router(replay.router)
    app.include_router(memories.admin_router)
    # SPEC 19.2 /metrics: HTTP metrics plus the orchestrix_* series this process records.
    Instrumentator(excluded_handlers=["/metrics", "/healthz"]).instrument(app).expose(
        app, include_in_schema=False
    )
    return with_event_schemas(app)


def with_event_schemas(app: FastAPI) -> FastAPI:
    """SPEC 17.2: SSE payloads are published in OpenAPI as one discriminated union (`RunEvent`),
    so the generated frontend client gets typed event handlers. The SSE route itself can only
    declare a generic text/event-stream body."""
    base = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            spec = base()
            event = run_event_adapter.json_schema(ref_template="#/components/schemas/{model}")
            schemas = spec.setdefault("components", {}).setdefault("schemas", {})
            schemas.update(event.pop("$defs", {}))
            schemas["RunEvent"] = event
            app.openapi_schema = spec
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def app() -> FastAPI:
    """Uvicorn factory entrypoint: `uvicorn --factory orchestrix_api.main:app`."""
    return create_app()
