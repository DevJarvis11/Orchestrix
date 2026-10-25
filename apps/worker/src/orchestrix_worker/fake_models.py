"""Deterministic fake models for dev and test (ORCHESTRIX_FAKE_MODELS=true; refused in production).

Lets the API end-to-end and the SIGKILL resilience test run a real worker without any model.
The fake specialist performs one idempotent "tool call" per subtask, keyed like real tool calls
(plan improvement 3), so a re-run after a crash finds its earlier side effect instead of repeating it.
"""

import asyncio
import hashlib
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.config import get_config
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_agents.context import RunContext, SpecialistOutcome, SpecialistRequest
from orchestrix_core.db.models import ToolCall
from orchestrix_core.schemas import ModelPrice, Plan, Review, Role, SubtaskResult, Usage

USAGE = {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280}

FAKE_PLAN = Plan.model_validate(
    {
        "goal": "Answer the request",
        "subtasks": [
            {
                "key": "s1",
                "description": "Research the topic",
                "specialist": "research",
                "expected_output": "markdown",
                "acceptance_criteria": ["has sources"],
                "complexity": "low",
            },
            {
                "key": "s2",
                "description": "Analyse the data",
                "specialist": "data_analyst",
                "expected_output": "table",
                "acceptance_criteria": ["has a table"],
                "complexity": "low",
            },
            {
                "key": "s3",
                "description": "Write the report",
                "specialist": "writer",
                "depends_on": ["s1", "s2"],
                "inputs": {"research": "s1.output", "analysis": "s2.output"},
                "expected_output": "markdown",
                "acceptance_criteria": ["cites sources"],
                "complexity": "low",
            },
        ],
        "deliverable_format": "markdown_report",
        "confidence": 0.9,
        "confidence_reason": "fake planner",
        "estimated_cost_usd": 0.0,
    }
)


class FakeModel(BaseChatModel):
    """Answers by schema: Plan -> FAKE_PLAN, Review -> approve; plain calls -> a short report."""

    @property
    def _llm_type(self) -> str:
        return "orchestrix-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = AIMessage(content="# Report\n\nFake deliverable.", usage_metadata=USAGE)  # type: ignore[arg-type]
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, AIMessage]:
        return self

    def with_structured_output(  # type: ignore[override]
        self, schema: Any, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[Any, Any]:
        def respond(_messages: Any) -> Any:
            parsed: Any
            if schema is Plan:
                parsed = FAKE_PLAN
            elif schema is Review:
                parsed = Review(verdict="approve", score=9, feedback="fake reviewer approves")
            else:
                raise NotImplementedError(f"fake model has no answer for {schema!r}")
            raw = AIMessage(
                content=json.dumps(parsed.model_dump(mode="json")), usage_metadata=USAGE
            )  # type: ignore[arg-type]
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(respond)


class FakeChatProvider:
    def chat(self, role: Role) -> BaseChatModel:
        return FakeModel()

    def model_name(self, role: Role) -> str:
        return "fake"

    def price(self, role: Role) -> ModelPrice | None:
        return ModelPrice(input_per_mtok=1.0, output_per_mtok=2.0)


class FakeToolSpecialist:
    """Sleeps (to widen the crash window), then records one idempotent fake tool call."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], delay_seconds: float) -> None:
        self._sessions = sessions
        self._delay = delay_seconds

    async def run(self, request: SpecialistRequest, ctx: RunContext) -> SpecialistOutcome:
        await asyncio.sleep(self._delay)
        checkpoint_ns = str(get_config().get("configurable", {}).get("checkpoint_ns", ""))
        key = hashlib.sha256(
            f"{ctx.run_id}|{checkpoint_ns}|fake.work:{request.subtask.key}|{request.feedback}|{request.hint}".encode()
        ).hexdigest()
        async with self._sessions.begin() as s:
            await s.execute(
                insert(ToolCall)
                .values(
                    run_id=UUID(ctx.run_id),
                    idempotency_key=key,
                    tool="fake.work",
                    server="fake",
                    args={"subtask": request.subtask.key},
                    output_preview=f"work for {request.subtask.key}",
                    status="ok",
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            call_id = (
                await s.execute(select(ToolCall.id).where(ToolCall.idempotency_key == key))
            ).scalar_one()
        result = SubtaskResult(
            key=request.subtask.key,
            output=f"Fake output for {request.subtask.key}.",
            sources=["https://example.com/fake"],
            tool_call_ids=[str(call_id)],
            confidence=0.9,
        )
        return SpecialistOutcome(
            result=result, usage=Usage(input_tokens=100, output_tokens=40, tool_calls=1)
        )
