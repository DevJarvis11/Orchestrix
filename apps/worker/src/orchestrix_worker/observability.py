"""Worker-side observability (SPEC 19): graph-node timings, run metrics, process start-up."""

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from prometheus_client import start_http_server

from orchestrix_core import metrics
from orchestrix_core.config import Settings
from orchestrix_core.logging import get_logger
from orchestrix_core.telemetry import init_tracing

log = get_logger(__name__)


class NodeTimer(BaseCallbackHandler):
    """orchestrix_node_duration_seconds{node}: LangGraph tags each node run with its name."""

    def __init__(self) -> None:
        self._started: dict[UUID, tuple[str, float]] = {}

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        node = (metadata or {}).get("langgraph_node")
        if node and name == node:
            self._started[run_id] = (str(node), time.monotonic())

    def _end(self, run_id: UUID) -> None:
        started = self._started.pop(run_id, None)
        if started is not None:
            metrics.NODE_DURATION.labels(started[0]).observe(time.monotonic() - started[1])

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end(run_id)


def run_finished(status: str, profile: str, active_seconds: float) -> None:
    metrics.RUNS.labels(status, profile).inc()
    metrics.RUN_DURATION.labels(status).observe(active_seconds)


def on_event(event: dict[str, Any]) -> None:
    if event.get("type") == "review.completed" and event.get("subtask"):
        metrics.REVIEW_SCORE.labels(str(event.get("specialist") or "unknown")).observe(
            float(event.get("score", 0))
        )


_started = False


def start_process(settings: Settings) -> None:
    """Once per worker process: tracing and the /metrics endpoint."""
    global _started
    if _started:
        return
    _started = True
    init_tracing("orchestrix-worker", settings)
    if settings.metrics_port:
        start_http_server(settings.metrics_port)
        log.info("metrics endpoint started", port=settings.metrics_port)
