"""App factory and lifespan (SPEC 9)."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from orchestrix_api.health import Check, database_check, http_check, redis_check
from orchestrix_api.health import router as health_router
from orchestrix_core.config import Settings, get_settings
from orchestrix_core.db.session import create_engine
from orchestrix_core.logging import configure_logging


def create_app(
    settings: Settings | None = None, readiness_checks: Mapping[str, Check] | None = None
) -> FastAPI:
    """Build the API. Tests pass `readiness_checks` to replace the real dependency probes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if readiness_checks is not None:
            app.state.readiness_checks = readiness_checks
            yield
            return
        cfg = settings or get_settings()
        engine = create_engine(cfg)
        redis = Redis.from_url(cfg.redis_url)  # pyright: ignore[reportUnknownMemberType]
        async with httpx.AsyncClient(timeout=2.0) as client:
            app.state.readiness_checks = {
                "database": database_check(engine),
                "redis": redis_check(redis),
                "opa": http_check(client, f"{cfg.opa_url}/health"),
                "litellm": http_check(client, f"{cfg.litellm_url}/health/liveliness"),
            }
            try:
                yield
            finally:
                await redis.aclose()
                await engine.dispose()

    configure_logging()
    app = FastAPI(title="Orchestrix API", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    return app


def app() -> FastAPI:
    """Uvicorn factory entrypoint: `uvicorn --factory orchestrix_api.main:app`."""
    return create_app()
