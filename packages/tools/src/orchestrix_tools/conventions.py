"""Shared MCP server conventions (SPEC 12.4): service-token auth, /healthz, structured errors."""

import hashlib
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

RUN_ID_HEADER = "x-run-id"
# `traceparent` is the W3C standard header.  Keep it lowercase because ASGI normalises headers
# that way, while HTTP clients may send any casing.
TRACE_HEADER = "traceparent"
TOOL_CALL_HEADER = "x-tool-call-id"
IDEMPOTENCY_HEADER = (
    "x-idempotency-key"  # hash(run_id, checkpoint_ns, tool_call_id), plan improvement 3
)

_IDEMPOTENCY_NS = uuid.UUID("2b7f9c0e-61a4-4d55-8f1e-9a0c3d5e7b21")


def idempotency_key(run_id: str, checkpoint_ns: str, tool_call_id: str) -> str:
    """hash(run_id, checkpoint_ns, tool_call_id): stable across re-runs (plan improvement 3).

    tool_call_id comes from the checkpointed model message, so a re-run after a crash produces
    the same key even though a fresh model call would have produced different arguments.
    """
    return hashlib.sha256(f"{run_id}|{checkpoint_ns}|{tool_call_id}".encode()).hexdigest()


def idempotent_id(ctx: Any) -> uuid.UUID:
    """A row id derived from the call's idempotency key, so a replayed call finds its first result."""
    key = request_header(ctx, IDEMPOTENCY_HEADER)
    return uuid.uuid5(_IDEMPOTENCY_NS, key) if key else uuid.uuid4()


def run_id_of(ctx: Any) -> uuid.UUID:
    raw = request_header(ctx, RUN_ID_HEADER)
    try:
        return uuid.UUID(raw or "")
    except ValueError as exc:
        raise tool_error("missing_run", "the X-Run-Id header is required") from exc


class ToolErrorPayload(BaseModel):
    """What an agent sees when a tool fails: never a stack trace (SPEC 12.4)."""

    error_code: str
    message: str
    retryable: bool


def tool_error(code: str, message: str, *, retryable: bool = False) -> ToolError:
    payload = ToolErrorPayload(error_code=code, message=message, retryable=retryable)
    return ToolError(payload.model_dump_json())


def parse_tool_error(text: str) -> ToolErrorPayload | None:
    try:
        return ToolErrorPayload.model_validate(json.loads(text))
    except (ValueError, TypeError):
        # FastMCP prefixes exceptions raised from a tool with its tool name.  Keep accepting
        # that documented wrapper so a server can normalise it at the protocol boundary.
        start = text.find("{")
        if start < 0:
            return None
        try:
            return ToolErrorPayload.model_validate(json.loads(text[start:]))
        except (ValueError, TypeError):
            return None


class OrchestrixFastMCP(FastMCP):
    """FastMCP with one safe, machine-readable error shape for every tool failure.

    FastMCP validates the annotated inputs with Pydantic, but its default validation exception
    is free text.  Normalising at ``call_tool`` retains Pydantic validation while ensuring an
    agent never has to parse framework-specific error messages (SPEC 12.4).
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            payload = parse_tool_error(str(exc))
            if payload is None:
                message = str(exc).splitlines()[0][:1000]
                code = "invalid_input" if "validation" in message.lower() else "tool_failed"
                payload = ToolErrorPayload(error_code=code, message=message, retryable=False)
            raise ToolError(payload.model_dump_json()) from exc


def new_server(name: str, instructions: str) -> FastMCP:
    # Behind the internal network and a bearer token; DNS-rebinding checks target browsers.
    return OrchestrixFastMCP(
        name=name,
        instructions=instructions,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class ServiceTokenAuth:
    """Only the worker's service token may call /mcp; /healthz stays open for the healthcheck."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if len(token) < 16:
            raise ValueError("MCP service token must be at least 16 characters")
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self._expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(
    server: FastMCP,
    token: str,
    ready: Callable[[], Awaitable[bool]] | None = None,
) -> ASGIApp:
    """Streamable HTTP at /mcp plus /healthz, wrapped in service-token auth."""

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        ok = await ready() if ready is not None else True
        return JSONResponse({"status": "ok" if ok else "unready"}, status_code=200 if ok else 503)

    app: Starlette = server.streamable_http_app()
    return ServiceTokenAuth(app, token)


def request_header(ctx: Any, name: str) -> str | None:
    """Read a request header inside a tool (Context.request_context.request is the HTTP request)."""
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    if request is None:
        return None
    value = request.headers.get(name)
    return str(value) if value is not None else None
