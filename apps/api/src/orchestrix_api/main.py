"""App factory and lifespan (SPEC 9)."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from orchestrix_api import errors
from orchestrix_api.deps import AppResources
from orchestrix_api.health import Check, database_check, http_check, redis_check
from orchestrix_api.health import router as health_router
from orchestrix_api.middleware import RequestIdMiddleware
from orchestrix_api.routers import models, runs, tasks
from orchestrix_core.config import Settings, get_settings
from orchestrix_core.db.repos.events import EventRepo
from orchestrix_core.db.repos.runs import RunRepo
from orchestrix_core.db.repos.tasks import TaskRepo, UserRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker
from orchestrix_core.events import EventBus
from orchestrix_core.logging import configure_logging


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
        engine = create_engine(cfg)
        sessions = create_sessionmaker(engine)
        redis = Redis.from_url(cfg.redis_url)  # pyright: ignore[reportUnknownMemberType]
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
                await engine.dispose()

    configure_logging()
    app = FastAPI(title="Orchestrix API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    errors.install(app)
    app.include_router(health_router)
    app.include_router(tasks.router)
    app.include_router(runs.router)
    app.include_router(models.router)
    return app


def app() -> FastAPI:
    """Uvicorn factory entrypoint: `uvicorn --factory orchestrix_api.main:app`."""
    return create_app()
