"""Specialist middleware pieces (FR-24, FR-63, SPEC 15.3): retry, budget estimate, budget reducer."""

import json

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from orchestrix_agents.middleware.budget import estimate_tokens
from orchestrix_agents.middleware.tools import ToolRetryMiddleware, is_retryable
from orchestrix_agents.state import raise_budget
from orchestrix_core.schemas import Budget


def error(retryable: bool) -> ToolMessage:
    payload = {"error_code": "x", "message": "m", "retryable": retryable}
    return ToolMessage(content=f"Error: {json.dumps(payload)}", tool_call_id="c", status="error")


def request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "web_fetch", "args": {}, "id": "c"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


def test_fr24_only_errors_marked_retryable_are_retried() -> None:
    assert is_retryable(error(True))
    assert not is_retryable(error(False))
    assert not is_retryable(ToolMessage(content='{"retryable": true}', tool_call_id="c"))  # success


async def test_fr24_retryable_errors_get_three_attempts() -> None:
    calls: list[int] = []

    async def handler(_: ToolCallRequest) -> ToolMessage:
        calls.append(1)
        return error(True)

    result = await ToolRetryMiddleware(base_seconds=0).awrap_tool_call(request(), handler)
    assert len(calls) == 3
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


async def test_fr24_non_retryable_errors_are_not_retried() -> None:
    calls: list[int] = []

    async def handler(_: ToolCallRequest) -> ToolMessage:
        calls.append(1)
        return error(False)

    await ToolRetryMiddleware(base_seconds=0).awrap_tool_call(request(), handler)
    assert len(calls) == 1


async def test_fr24_transport_failure_recovers_then_succeeds() -> None:
    attempts: list[int] = []

    async def handler(_: ToolCallRequest) -> ToolMessage:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.ConnectError("reset")
        return ToolMessage(content="ok", tool_call_id="c")

    result = await ToolRetryMiddleware(base_seconds=0).awrap_tool_call(request(), handler)
    assert len(attempts) == 2
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


async def test_fr24_transport_failure_becomes_a_retryable_tool_error() -> None:
    async def handler(_: ToolCallRequest) -> ToolMessage:
        raise httpx.ConnectError("down")

    result = await ToolRetryMiddleware(base_seconds=0).awrap_tool_call(request(), handler)
    assert isinstance(result, ToolMessage)
    assert json.loads(str(result.content))["error_code"] == "transport_failed"


def test_fr63_estimate_overestimates_rather_than_under() -> None:
    text = "word " * 400  # ~400 real tokens
    estimate = estimate_tokens([HumanMessage(text), AIMessage("ok")], "system prompt")
    assert estimate >= 400


def test_fr63_budget_reducer_keeps_first_value_and_only_raises() -> None:
    small = Budget(max_tokens=100)
    assert raise_budget(None, small) == small  # a small task budget is not replaced by defaults
    raised = raise_budget(small, Budget(max_tokens=1_000, max_usd=0.5))
    assert (raised.max_tokens, raised.max_usd) == (1_000, small.max_usd)
