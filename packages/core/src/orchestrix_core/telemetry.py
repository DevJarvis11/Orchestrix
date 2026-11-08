"""Tracing (SPEC 19.1, FR-60): OpenTelemetry with Langfuse as the exporter.

One trace per run, whose id is derived from the run id, so a run's spans join up across
resumes and workers. Langfuse's LangChain handler yields nested spans for graph nodes, model
and tool calls; Orchestrix adds its own spans (policy, memory, MCP, escalations, budget) on
the same tracer provider. PII is redacted before export (FR-93). Without Langfuse keys every
helper here is a cheap no-op, and runs never block on tracing.
"""

import contextlib
from collections.abc import Generator
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from orchestrix_core.config import Settings
from orchestrix_core.logging import get_logger
from orchestrix_core.pii import Redactor

log = get_logger(__name__)

_enabled = False
TRACER = "orchestrix"


def tracing_enabled() -> bool:
    return _enabled


def init_tracing(service: str, settings: Settings) -> bool:
    """Once per process. Returns False (no-op tracing) when Langfuse is not configured."""
    global _enabled
    if _enabled:
        return True
    if not (
        settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key
    ):
        return False
    from langfuse import Langfuse

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    trace.set_tracer_provider(provider)
    redactor = Redactor(settings.pii_patterns)

    def mask(*, data: Any, **_: Any) -> Any:  # FR-93: redact before export
        return redactor.any(data)

    Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        tracer_provider=provider,
        environment=settings.env,
        release=settings.graph_version,
        mask=mask,
    )
    _enabled = True
    log.info("tracing to langfuse", host=settings.langfuse_host, service=service)
    return True


def run_trace_id(run_id: str) -> str:
    """32 hex chars, deterministic per run (W3C trace id)."""
    from langfuse import Langfuse

    return Langfuse.create_trace_id(seed=run_id)


@contextlib.contextmanager
def run_trace(run_id: str) -> Generator[None]:
    """Make spans created inside the block children of the run's trace."""
    if not _enabled:
        yield
        return
    parent = SpanContext(
        trace_id=int(run_trace_id(run_id), 16),
        span_id=int(run_trace_id(f"{run_id}:root")[:16], 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    token = otel_context.attach(trace.set_span_in_context(NonRecordingSpan(parent)))
    try:
        yield
    finally:
        otel_context.detach(token)


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Generator[Any]:
    """An Orchestrix span (policy, memory, mcp, escalation, budget), or nothing when disabled."""
    if not _enabled:
        yield None
        return
    tracer = trace.get_tracer(TRACER)
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(
                    key, value if isinstance(value, str | int | float | bool) else str(value)
                )
        yield current


def langchain_handler(run_id: str) -> Any | None:
    """Langfuse's LangChain callback for the graph config, bound to the run's trace."""
    if not _enabled:
        return None
    from langfuse.langchain import CallbackHandler

    return CallbackHandler(trace_context={"trace_id": run_trace_id(run_id)})


def current_ids() -> tuple[str | None, str | None]:
    """(trace_id, span_id) of the current span, for llm_calls/tool_calls rows."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


def current_traceparent() -> str | None:
    """The W3C traceparent of the current span, for MCP calls (SPEC 12.4)."""
    if not _enabled:
        return None
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def flush() -> None:
    if _enabled:
        from langfuse import get_client

        get_client().flush()
