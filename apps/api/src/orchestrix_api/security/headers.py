"""Security headers, CORS and rate limits (SPEC 15.4)."""

import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        return response


MAX_BODY = 1024 * 1024  # SPEC 15.4: 1 MB; attachments use the upload endpoint
MAX_UPLOAD_BODY = 11 * 1024 * 1024  # one 10 MB file plus multipart overhead


class BodyLimit(BaseHTTPMiddleware):
    """Rejects declared oversize bodies early; the upload route also caps what it reads."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limit = MAX_UPLOAD_BODY if request.url.path.startswith("/v1/uploads") else MAX_BODY
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return Response(
                '{"type":"about:blank","title":"Content Too Large","status":413}',
                status_code=413,
                media_type="application/problem+json",
            )
        return await call_next(request)


def install(app: FastAPI, origins: list[str]) -> None:
    app.add_middleware(BodyLimit)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,  # strict allow-list
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"],
        expose_headers=["X-Request-Id", "traceparent"],
    )


async def allow(redis: Redis, key: str, limit: int, window_seconds: int) -> bool:
    """Sliding-window rate limit in Redis (one sorted set per user and limit). Fails open on a
    Redis outage: losing rate limiting is safer than losing the API."""
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - window_seconds)  # pyright: ignore[reportUnknownMemberType]
            pipe.zadd(key, {member: now})  # pyright: ignore[reportUnknownMemberType]
            pipe.zcard(key)  # pyright: ignore[reportUnknownMemberType]
            pipe.expire(key, window_seconds)  # pyright: ignore[reportUnknownMemberType]
            results = await pipe.execute()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    except RedisError:
        return True
    return int(results[2]) <= limit  # pyright: ignore[reportUnknownArgumentType]
