"""Worker-side MCP client (SPEC 12.4): MultiServerMCPClient, loaded once, filtered per specialist."""

import base64
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from typing import Any, Protocol

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langgraph.config import get_config
from mcp.types import CallToolResult, TextContent

from orchestrix_core.schemas import Specialist
from orchestrix_core.telemetry import current_traceparent, span
from orchestrix_tools.catalogue import CATALOGUE, canonical
from orchestrix_tools.conventions import ToolErrorPayload, idempotency_key

CURRENT_RUN: ContextVar[str | None] = ContextVar("orchestrix_current_run", default=None)
"""Set by the specialist runner so shared, cached tools know which run they serve."""

_TRACE_NAMESPACE = uuid.UUID("eedaa750-22c2-4a6b-a551-91c8f071c3fe")


def traceparent_for(run_id: str, tool_call_id: str) -> str:
    """A deterministic W3C trace context until M5 installs OpenTelemetry propagation.

    The trace id is stable for the run and the span id is stable for a checkpointed tool call,
    which lets MCP servers correlate logs without introducing an observability dependency early.
    """
    trace_id = uuid.uuid5(_TRACE_NAMESPACE, run_id).hex
    span_id = hashlib.sha256(f"{run_id}|{tool_call_id}".encode()).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01"


def current_checkpoint_ns() -> str:
    try:
        return str(get_config().get("configurable", {}).get("checkpoint_ns", ""))
    except RuntimeError:  # outside a graph (unit tests)
        return ""


def current_call_key(request: MCPToolCallRequest) -> str | None:
    """The plan-improvement-3 idempotency key of the tool call being executed, if known."""
    run_id = CURRENT_RUN.get()
    tool_call_id = getattr(request.runtime, "tool_call_id", None)
    if not run_id or not tool_call_id:
        return None
    return idempotency_key(run_id, current_checkpoint_ns(), str(tool_call_id))


def error_result(code: str, message: str, *, retryable: bool = False) -> CallToolResult:
    payload = ToolErrorPayload(error_code=code, message=message, retryable=retryable)
    return CallToolResult(
        content=[TextContent(type="text", text=payload.model_dump_json())], isError=True
    )


class ArtifactBridge(Protocol):
    """Worker-side artifact I/O for the sandbox, which itself has no storage access."""

    async def read_for_run(self, run_id: str, artifact_ids: list[str]) -> list[tuple[str, bytes]]:
        """(name, bytes) per id; raises LookupError if the run may not read one of them."""
        ...

    async def store_created(
        self, run_id: str, call_key: str, files: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Store files returned by the sandbox; returns their metadata with artifact ids."""
        ...


SANDBOX_TOOL: tuple[str, str] = ("sandbox", "run_python")
HIDDEN_ARGS: dict[tuple[str, str], tuple[str, ...]] = {
    SANDBOX_TOOL: ("input_files",)
}  # filled by the platform, never by the model


class SandboxFiles:
    """Moves file bytes between S3 and the sandbox so the sandbox needs no credentials."""

    def __init__(self, bridge: ArtifactBridge) -> None:
        self._bridge = bridge

    async def __call__(
        self, request: MCPToolCallRequest, handler: Callable[[MCPToolCallRequest], Awaitable[Any]]
    ) -> Any:
        if (request.server_name, request.name) != SANDBOX_TOOL:
            return await handler(request)
        run_id = CURRENT_RUN.get()
        key = current_call_key(request)
        if not run_id or not key:
            return error_result("missing_run", "sandbox calls need a run")
        raw_ids: list[Any] = list(request.args.get("artifact_ids") or [])
        ids = [str(i) for i in raw_ids]
        try:
            files = await self._bridge.read_for_run(run_id, ids)
        except LookupError as exc:
            return error_result("artifact_not_found", str(exc))
        args = {
            **request.args,
            "input_files": [
                {"name": name, "content_b64": base64.b64encode(body).decode()}
                for name, body in files
            ],
        }
        result = await handler(request.override(args=args))
        if not isinstance(result, CallToolResult) or result.isError:
            return result
        structured: dict[str, Any] = dict(result.structuredContent or {})
        created: list[dict[str, Any]] = list(structured.pop("created_files", []))
        structured["created_artifacts"] = await self._bridge.store_created(run_id, key, created)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(structured))],
            structuredContent=structured,
            isError=False,
        )


class RunHeaders:
    """Adds X-Run-Id and X-Idempotency-Key to every MCP call (SPEC 12.4, plan improvement 3)."""

    async def __call__(
        self, request: MCPToolCallRequest, handler: Callable[[MCPToolCallRequest], Awaitable[Any]]
    ) -> Any:
        headers: dict[str, Any] = dict(request.headers or {})
        run_id = CURRENT_RUN.get()
        if run_id:
            headers["X-Run-Id"] = run_id
            tool_call_id = getattr(request.runtime, "tool_call_id", None)
            if tool_call_id:
                headers["X-Idempotency-Key"] = idempotency_key(
                    run_id, current_checkpoint_ns(), str(tool_call_id)
                )
        with span("mcp.call", **{"orchestrix.tool": f"{request.server_name}.{request.name}"}):
            if run_id:
                tool_call_id = getattr(request.runtime, "tool_call_id", None)
                headers["traceparent"] = current_traceparent() or traceparent_for(
                    run_id, str(tool_call_id or "")
                )
            return await handler(request.override(headers=headers))


class ToolBox:
    def __init__(
        self, urls: dict[str, str], service_token: str, bridge: ArtifactBridge | None = None
    ) -> None:
        interceptors: list[Any] = [RunHeaders()]
        if bridge is not None:
            interceptors.append(SandboxFiles(bridge))
        self._client = MultiServerMCPClient(
            {
                name: {
                    "transport": "streamable_http",
                    "url": url,
                    "headers": {"Authorization": f"Bearer {service_token}"},
                    "timeout": 90,
                }
                for name, url in urls.items()
            },
            tool_name_prefix=True,  # web_search, data_sql_query, ... (see catalogue.canonical)
            tool_interceptors=interceptors,
        )
        self._tools: dict[str, BaseTool] = {}

    async def load(self) -> None:
        """Fetch tool schemas from every server once (SPEC 12.4: cached at worker start)."""
        tools = await self._client.get_tools()
        for t in tools:
            server, _, name = t.name.partition("_")
            hidden = HIDDEN_ARGS.get((server, name))
            if hidden and isinstance(t.args_schema, dict):
                schema: dict[str, Any] = dict(t.args_schema)
                schema["properties"] = {
                    k: v for k, v in dict(schema.get("properties", {})).items() if k not in hidden
                }
                schema["required"] = [r for r in schema.get("required", []) if r not in hidden]
                t.args_schema = schema
        self._tools = {canonical(t.name): t for t in tools}

    @property
    def loaded(self) -> dict[str, BaseTool]:
        return dict(self._tools)

    def for_specialist(
        self, specialist: Specialist, enabled: Iterable[str] | None = None
    ) -> list[BaseTool]:
        """Tools on the specialist's allow-list (FR-22), minus any an admin disabled."""
        allowed = {n for n, spec in CATALOGUE.items() if specialist in spec.allowed_specialists}
        if enabled is not None:
            allowed &= set(enabled)
        return [tool for name, tool in sorted(self._tools.items()) if name in allowed]
