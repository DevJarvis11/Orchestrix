"""gateway-check probe logic against a mocked LiteLLM proxy (SPEC 16.2, amendment A1)."""

import json
from typing import Any

import httpx
import pytest
import respx

from orchestrix_core import gateway_check as gc
from orchestrix_core.db.models import EMBEDDING_DIM

BASE = "http://litellm.test"
RERANKED = {
    "results": [{"index": 1, "relevance_score": 0.99}, {"index": 0, "relevance_score": 0.68}]
}


def completion(model: str, content: str | None = None, tool: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [
            {"type": "function", "function": {"name": tool, "arguments": "{}"}}
        ]
    return {"model": model, "choices": [{"message": message}]}


def healthy_proxy(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    model = body["model"]
    if model == gc.FALLBACK_PROBE_MODEL:
        headers = {"x-litellm-attempted-fallbacks": "1", "x-litellm-model-group": gc.LOCAL_MODEL}
        return httpx.Response(200, json=completion("ollama_chat/qwen3:8b", "pong"), headers=headers)
    if "tools" in body:
        return httpx.Response(200, json=completion(model, None, "get_weather"))
    if "response_format" in body:
        return httpx.Response(200, json=completion(model, '{"answer": 5}'))
    return httpx.Response(200, json=completion(model, "pong"))


async def run_against(handler: Any, local_only: bool = False) -> dict[str, gc.Result]:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/v1/chat/completions").mock(side_effect=handler)
        mock.post("/v1/embeddings").respond(
            200, json={"data": [{"embedding": [0.0] * EMBEDDING_DIM}]}
        )
        mock.post("/v1/rerank").respond(200, json=RERANKED)
        async with httpx.AsyncClient(base_url=BASE) as client:
            results = await gc.run(client, gc.build_probes(local_only))
    return {r.name: r for r in results}


async def test_all_probes_pass_on_a_healthy_gateway() -> None:
    results = await run_against(healthy_proxy)
    assert all(r.ok for r in results.values()), results
    assert len(results) == 9  # 3 per chat model, embed, rerank, fallback


async def test_local_only_skips_yotta_models() -> None:
    results = await run_against(healthy_proxy, local_only=True)
    assert not any(
        name.startswith((gc.REMOTE_MODEL, gc.EMBED_MODEL, gc.RERANK_MODEL)) for name in results
    )


async def test_remote_requests_carry_reasoning_effort() -> None:
    seen: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return healthy_proxy(request)

    await run_against(record)
    remote = [b for b in seen if b["model"] == gc.REMOTE_MODEL]
    local = [b for b in seen if b["model"] == gc.LOCAL_MODEL]
    assert remote
    assert all(b["reasoning_effort"] == "low" for b in remote)
    assert all("reasoning_effort" not in b for b in local)


@pytest.mark.parametrize(
    ("probe", "reply", "error"),
    [
        ("json_schema", completion("m", "not json"), "not schema-valid"),
        ("json_schema", completion("m", '{"answer": 4}'), "wrong answer"),
        ("tool_call", completion("m", "it is sunny"), "no get_weather"),
        ("chat", completion("m", "  "), "empty content"),
        ("chat", {"error": "boom"}, "malformed"),
    ],
)
async def test_bad_replies_fail_loudly(probe: str, reply: dict[str, Any], error: str) -> None:
    results = await run_against(lambda _req: httpx.Response(200, json=reply), local_only=True)
    result = results[f"{gc.LOCAL_MODEL} {probe}"]
    assert not result.ok
    assert error in result.detail


async def test_http_error_is_reported() -> None:
    results = await run_against(lambda _req: httpx.Response(401, text="bad key"), local_only=True)
    assert "HTTP 401" in results[f"{gc.LOCAL_MODEL} chat"].detail


async def test_wrong_embedding_dimension_fails() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/embeddings").respond(200, json={"data": [{"embedding": [0.0] * 1024}]})
        async with httpx.AsyncClient(base_url=BASE) as client:
            [result] = await gc.run(client, [("embed", gc.embed_probe())])
    assert not result.ok
    assert "1024 != 2560" in result.detail


async def test_fallback_must_be_served_by_local_model() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/chat/completions").respond(
            200,
            json=completion("x", "pong"),
            headers={
                "x-litellm-attempted-fallbacks": "0",
                "x-litellm-model-group": "fallback-probe",
            },
        )
        async with httpx.AsyncClient(base_url=BASE) as client:
            [result] = await gc.run(client, [("fallback", gc.fallback_probe())])
    assert not result.ok
    assert "no fallback" in result.detail


async def test_direct_probe_fails_when_fallback_answered() -> None:
    headers = {"x-litellm-attempted-fallbacks": "1", "x-litellm-model-group": gc.LOCAL_MODEL}
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/chat/completions").respond(
            200, json=completion("q", "pong"), headers=headers
        )
        async with httpx.AsyncClient(base_url=BASE) as client:
            [result] = await gc.run(client, [("chat", gc.chat_probe(gc.REMOTE_MODEL))])
    assert not result.ok
    assert "answered by fallback qwen3-8b-local" in result.detail


async def test_rerank_must_put_relevant_document_first() -> None:
    wrong = {
        "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}]
    }
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/rerank").respond(200, json=wrong)
        async with httpx.AsyncClient(base_url=BASE) as client:
            [result] = await gc.run(client, [("rerank", gc.rerank_probe())])
    assert not result.ok
    assert "wrong document first" in result.detail
