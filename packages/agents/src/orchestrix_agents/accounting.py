"""LLM call accounting (SPEC 15.3, 19.1; FR-62; amendment A6).

A LangChain callback attached to every gateway chat model. When a call ends it writes one
`llm_calls` row (tokens, cost, latency, requested vs served model, agent, purpose, subtask,
attempt, revision) and emits `llm.completed`. Cost: see `usage_of`.
"""

import contextlib
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.config import get_stream_writer

from orchestrix_agents.context import LlmCallSink
from orchestrix_agents.llm import served_model, usage_of
from orchestrix_core import metrics
from orchestrix_core.logging import get_logger
from orchestrix_core.schemas import CallTag, LlmCallRecord, ModelPrice
from orchestrix_core.schemas.events import LlmCompletedEvent
from orchestrix_core.telemetry import current_ids

log = get_logger(__name__)


class LlmCallRecorder(AsyncCallbackHandler):
    """One per chat model instance: knows the run, role and requested model it serves."""

    raise_error = False  # accounting must never break a run

    def __init__(
        self,
        *,
        sink: LlmCallSink | None,
        run_id: str,
        role: str,
        requested_model: str,
        tag: CallTag,
        prices: dict[str, ModelPrice],
        team: str = "",
    ) -> None:
        self._team = team
        self._sink = sink
        self._run_id = run_id
        self._role = role
        self._requested = requested_model
        self._tag = tag
        self._prices = prices
        self._started: dict[UUID, float] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._started[run_id] = time.monotonic()

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started.pop(run_id, time.monotonic())
        message: AIMessage | None = None
        for generations in response.generations:
            for g in generations:
                if isinstance(g, ChatGeneration) and isinstance(g.message, AIMessage):
                    message = g.message
        if message is None:
            return
        served = served_model(message) or self._requested
        usage = usage_of(message, self._prices.get(served) or self._prices.get(self._requested))
        trace_id, span_id = current_ids()
        record = LlmCallRecord(
            trace_id=trace_id,
            span_id=span_id,
            **self._tag.model_dump(),
            run_id=self._run_id,
            model_alias=self._role,
            requested_model=self._requested,
            model_id=served,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.usd,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        metrics.LLM_TOKENS.labels(record.agent, self._role, "in").inc(record.input_tokens)
        metrics.LLM_TOKENS.labels(record.agent, self._role, "out").inc(record.output_tokens)
        metrics.LLM_COST.labels(record.agent, self._role, self._team).inc(record.cost_usd)
        with contextlib.suppress(RuntimeError):  # no stream outside a graph run
            get_stream_writer()(
                LlmCompletedEvent(
                    type="llm.completed",
                    run_id=self._run_id,
                    agent=record.agent,
                    model_alias=record.model_alias,
                    model_id=record.model_id,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cost_usd=record.cost_usd,
                ).model_dump(mode="json")
            )
        if self._sink is not None:
            try:
                await self._sink.record(record)
            except Exception as exc:  # accounting is best-effort; the run continues
                log.warning("llm_calls write failed", error=str(exc), run_id=self._run_id)
