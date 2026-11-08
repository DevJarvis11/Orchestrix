"""Tool-call middleware for specialists (SPEC 12.2, 15.2; FR-23, FR-24; plan improvement 3).

Order inside create_agent (first = outermost):
    ToolCallLimit -> InjectionGuard -> ToolAccounting -> Policy -> ToolRetry -> the MCP tool.
Accounting sits outside Policy so a call that already ran is answered from its stored row and
never asks a human twice; it stores raw output, and the guard wraps whatever the model sees
(stored or fresh). Retry sits innermost so one tool_calls row records the final outcome.
"""

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, cast

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from orchestrix_agents.context import ToolCallStore
from orchestrix_agents.middleware.injection import scan, wrap_untrusted
from orchestrix_core import metrics
from orchestrix_core.schemas import EscalationRecord
from orchestrix_core.schemas.events import ToolEvent
from orchestrix_core.telemetry import current_ids
from orchestrix_tools.catalogue import canonical
from orchestrix_tools.client import current_checkpoint_ns
from orchestrix_tools.conventions import idempotency_key

# Content from these tools is untrusted input (SPEC 15.2): web pages and user uploads.
TAINTING_TOOLS: Final = frozenset({"web.fetch", "web.search", "files.read_artifact"})

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]


def call_key(run_id: str, request: ToolCallRequest) -> str:
    return idempotency_key(run_id, current_checkpoint_ns(), str(request.tool_call.get("id", "")))


def _text(message: ToolMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(
        str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content
    )


def _emit(event: ToolEvent) -> None:
    with contextlib.suppress(RuntimeError):  # outside a graph run there is no stream
        get_stream_writer()(event.model_dump(mode="json"))


@dataclass
class CallLog:
    """What one specialist run did with tools; read back by the specialist runner."""

    tool_call_ids: list[str] = field(default_factory=list[str])
    tainted: bool = False
    warnings: list[str] = field(default_factory=list[str])
    tool_calls: int = 0
    escalations: list[EscalationRecord] = field(default_factory=list[EscalationRecord])


class ToolAccountingMiddleware(AgentMiddleware):  # pyright: ignore[reportMissingTypeArgument]
    """FR-23: one tool_calls row per call. A replayed key returns the stored result instead."""

    def __init__(self, store: ToolCallStore, run_id: str, subtask_key: str, log: CallLog) -> None:
        super().__init__()
        self._store = store
        self._run_id = run_id
        self._subtask = subtask_key
        self._log = log

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command[Any]:
        name = canonical(request.tool_call["name"])
        key = call_key(self._run_id, request)
        call_id = str(request.tool_call.get("id", ""))
        stored = await self._store.find(key)
        if stored is not None:  # crash or resume replay: never repeat the side effect
            self._log.tool_call_ids.append(stored.id)
            return ToolMessage(
                content=stored.output,
                tool_call_id=call_id,
                name=request.tool_call["name"],
                status="success" if stored.status == "ok" else "error",
            )

        _emit(ToolEvent(type="tool.called", run_id=self._run_id, tool=name, subtask=self._subtask))
        started = time.monotonic()
        trace_id, span_id = current_ids()
        result = await handler(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        if not isinstance(result, ToolMessage):
            return result
        output = _text(result)
        status = "error" if result.status == "error" else "ok"
        raw: Any = result.artifact
        artifact: dict[str, Any] = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        status = str(artifact.get("orchestrix_status") or status)
        row_id = await self._store.record(
            run_id=self._run_id,
            subtask_key=self._subtask,
            idempotency_key=key,
            tool=name,
            args=dict(artifact.get("executed_args") or request.tool_call.get("args", {})),
            output=output,
            status=status,
            latency_ms=latency_ms,
            error=output[:2000] if status != "ok" else None,
            policy_decision=artifact.get("policy_decision"),
            trace_id=trace_id,
            span_id=span_id,
        )
        metrics.TOOL_CALLS.labels(name, status).inc()
        metrics.TOOL_LATENCY.labels(name).observe(latency_ms / 1000)
        self._log.tool_call_ids.append(row_id)
        self._log.tool_calls += 1
        _emit(
            ToolEvent(
                type="tool.denied" if status == "denied" else "tool.completed",
                run_id=self._run_id,
                tool=name,
                subtask=self._subtask,
                status=status,
                latency_ms=latency_ms,
            )
        )
        return result


class InjectionGuardMiddleware(AgentMiddleware):  # pyright: ignore[reportMissingTypeArgument]
    """SPEC 15.2: delimit tool output as untrusted data, scan it, and taint the subtask."""

    def __init__(self, log: CallLog) -> None:
        super().__init__()
        self._log = log

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result
        name = canonical(request.tool_call["name"])
        text = _text(result)
        findings = scan(text)
        if findings:
            self._log.warnings.append(f"injection_suspected in {name}: {', '.join(findings)}")
        if name in TAINTING_TOOLS:
            self._log.tainted = True
        return result.model_copy(update={"content": wrap_untrusted(name, text, findings)})


RETRY_ATTEMPTS: Final = 3
RETRY_BASE_SECONDS: Final = 0.5
RETRY_MAX_SECONDS: Final = 8.0


def is_retryable(result: ToolMessage) -> bool:
    """FR-24: the server's {error_code, message, retryable} says so (timeouts, 429, 5xx)."""
    if result.status != "error":
        return False
    text = _text(result)
    try:
        payload: Any = json.loads(text[text.index("{") :])
    except ValueError:
        return False
    return isinstance(payload, dict) and bool(cast(dict[str, Any], payload).get("retryable"))


def _transport_failure(request: ToolCallRequest, exc: Exception) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {
                "error_code": "transport_failed",
                "message": f"{type(exc).__name__}: {exc}"[:500],
                "retryable": True,
            }
        ),
        tool_call_id=str(request.tool_call.get("id", "")),
        name=request.tool_call["name"],
        status="error",
    )


class ToolRetryMiddleware(AgentMiddleware):  # pyright: ignore[reportMissingTypeArgument]
    """FR-24: retryable tool errors get 3 attempts with exponential backoff and full jitter.

    MCP errors arrive as error ToolMessages, which the built-in ToolRetryMiddleware (exceptions
    only) never sees. Every attempt carries the same idempotency key, so a side-effecting
    server dedupes a retry that actually landed. Graph control flow (interrupts) is re-raised.
    """

    def __init__(self, attempts: int = RETRY_ATTEMPTS, base_seconds: float = RETRY_BASE_SECONDS):
        super().__init__()
        self._attempts = attempts
        self._base = base_seconds

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command[Any]:
        result: ToolMessage | Command[Any] | None = None
        for attempt in range(self._attempts):
            if attempt:
                cap = min(RETRY_MAX_SECONDS, self._base * 2 ** (attempt - 1))
                await asyncio.sleep(random.uniform(0, cap))  # noqa: S311 - jitter, not crypto
            try:
                result = await handler(request)
            except GraphBubbleUp:
                raise
            except Exception as exc:  # transport or session failure: retryable by nature
                result = _transport_failure(request, exc)
                continue
            if not (isinstance(result, ToolMessage) and is_retryable(result)):
                return result
        assert result is not None  # noqa: S101 - attempts >= 1
        return result
