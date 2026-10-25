"""`make gateway-check`: probe every catalogue model through the LiteLLM proxy (SPEC 16.2, A1).

For each chat model: a plain chat, a json_schema structured output and a tool call. Then an
embedding (must be EMBEDDING_DIM wide), a rerank (must rank the relevant document first) and a
forced fallback from the VPN model to host Ollama. Exits non-zero on any failure.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from orchestrix_core.config import get_settings
from orchestrix_core.db.models import EMBEDDING_DIM

REMOTE_MODEL = "gpt-oss-120b"
LOCAL_MODEL = "qwen3-8b-local"
EMBED_MODEL = "embed"
RERANK_MODEL = "rerank"
FALLBACK_PROBE_MODEL = "fallback-probe"  # unreachable deployment that must fall back to local

# Per-model extras the app also sends (A2): effort is a gpt-oss knob.
MODEL_PARAMS: dict[str, dict[str, Any]] = {REMOTE_MODEL: {"reasoning_effort": "low"}}

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


class Sum(BaseModel):
    answer: int


class CheckFailed(Exception):
    pass


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    seconds: float


Probe = Callable[[httpx.AsyncClient], Awaitable[str]]


def _message(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CheckFailed(f"malformed response: {str(body)[:200]}") from exc


async def _chat(client: httpx.AsyncClient, model: str, **payload: Any) -> dict[str, Any]:
    response = await client.post(
        "/v1/chat/completions", json={"model": model, **MODEL_PARAMS.get(model, {}), **payload}
    )
    if response.status_code != 200:
        raise CheckFailed(f"HTTP {response.status_code}: {response.text[:300]}")
    # A probe of one model must be answered by that model, not silently by the fallback.
    fallbacks = int(response.headers.get("x-litellm-attempted-fallbacks", "0"))
    if fallbacks:
        group = response.headers.get("x-litellm-model-group")
        raise CheckFailed(f"{model} unavailable: answered by fallback {group}")
    return response.json()


def chat_probe(model: str) -> Probe:
    async def probe(client: httpx.AsyncClient) -> str:
        body = await _chat(
            client,
            model,
            messages=[{"role": "user", "content": "Reply with exactly one word: pong"}],
            max_tokens=256,
        )
        content = (_message(body).get("content") or "").strip()
        if not content:
            raise CheckFailed("empty content")
        return f"served by {body.get('model')}: {content[:40]!r}"

    return probe


def structured_probe(model: str) -> Probe:
    async def probe(client: httpx.AsyncClient) -> str:
        body = await _chat(
            client,
            model,
            messages=[{"role": "user", "content": "What is 2 + 3? Answer in JSON."}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "sum", "schema": Sum.model_json_schema(), "strict": True},
            },
            max_tokens=512,
        )
        content = _message(body).get("content") or ""
        try:
            parsed = Sum.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise CheckFailed(f"not schema-valid JSON: {content[:200]!r}") from exc
        if parsed.answer != 5:
            raise CheckFailed(f"wrong answer {parsed.answer}")
        return f"served by {body.get('model')}: {parsed.model_dump_json()}"

    return probe


def tool_probe(model: str) -> Probe:
    async def probe(client: httpx.AsyncClient) -> str:
        body = await _chat(
            client,
            model,
            messages=[{"role": "user", "content": "What is the weather in Paris right now?"}],
            tools=[WEATHER_TOOL],
            tool_choice="auto",
            max_tokens=512,
        )
        calls: list[dict[str, Any]] = _message(body).get("tool_calls") or []
        names = [str(c.get("function", {}).get("name")) for c in calls]
        if "get_weather" not in names:
            raise CheckFailed(f"no get_weather tool call (got {names})")
        return f"served by {body.get('model')}: tool_calls={names}"

    return probe


def embed_probe() -> Probe:
    async def probe(client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/v1/embeddings", json={"model": EMBED_MODEL, "input": ["orchestrix gateway check"]}
        )
        if response.status_code != 200:
            raise CheckFailed(f"HTTP {response.status_code}: {response.text[:300]}")
        vector = response.json()["data"][0]["embedding"]
        if len(vector) != EMBEDDING_DIM:
            raise CheckFailed(f"dimension {len(vector)} != {EMBEDDING_DIM}")
        return f"{len(vector)} dims"

    return probe


def rerank_probe() -> Probe:
    async def probe(client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/v1/rerank",
            json={
                "model": RERANK_MODEL,
                "query": "What is the capital of France?",
                "documents": ["Berlin is in Germany.", "Paris is the capital of France."],
            },
        )
        if response.status_code != 200:
            raise CheckFailed(f"HTTP {response.status_code}: {response.text[:300]}")
        results: list[dict[str, Any]] = response.json()["results"]
        best = max(results, key=lambda r: float(r["relevance_score"]))
        if best["index"] != 1:
            raise CheckFailed(f"ranked the wrong document first: {results}")
        return f"top score {float(best['relevance_score']):.3f}"

    return probe


def fallback_probe() -> Probe:
    async def probe(client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": FALLBACK_PROBE_MODEL,
                "messages": [{"role": "user", "content": "Reply with exactly one word: pong"}],
                "max_tokens": 256,
            },
        )
        if response.status_code != 200:
            raise CheckFailed(f"HTTP {response.status_code}: {response.text[:300]}")
        attempted = int(response.headers.get("x-litellm-attempted-fallbacks", "0"))
        group = response.headers.get("x-litellm-model-group")
        if attempted < 1 or group != LOCAL_MODEL:
            raise CheckFailed(
                f"no fallback to {LOCAL_MODEL} (attempted={attempted}, group={group})"
            )
        return f"{FALLBACK_PROBE_MODEL} -> {group}"

    return probe


def build_probes(local_only: bool) -> list[tuple[str, Probe]]:
    models = [LOCAL_MODEL] if local_only else [REMOTE_MODEL, LOCAL_MODEL]
    probes: list[tuple[str, Probe]] = []
    for model in models:
        probes += [
            (f"{model} chat", chat_probe(model)),
            (f"{model} json_schema", structured_probe(model)),
            (f"{model} tool_call", tool_probe(model)),
        ]
    if not local_only:  # embeddings and rerank live on Yotta (A5)
        probes.append((f"{EMBED_MODEL} embedding", embed_probe()))
        probes.append((f"{RERANK_MODEL} ranking", rerank_probe()))
    probes.append(("fallback to local", fallback_probe()))
    return probes


async def run(client: httpx.AsyncClient, probes: list[tuple[str, Probe]]) -> list[Result]:
    results: list[Result] = []
    for name, probe in probes:
        start = time.perf_counter()
        try:
            detail, ok = await probe(client), True
        except (CheckFailed, httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            detail, ok = f"{type(exc).__name__}: {exc}", False
        results.append(Result(name, ok, detail, time.perf_counter() - start))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true", help="skip the Yotta (VPN) models")
    args = parser.parse_args()

    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.litellm_master_key.get_secret_value()}"}

    async def go() -> list[Result]:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url,
            headers=headers,
            timeout=360,  # > proxy timeouts
        ) as client:
            return await run(client, build_probes(args.local_only))

    results = asyncio.run(go())
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name:<30} {r.seconds:6.1f}s  {r.detail}")
    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\ngateway-check FAILED: {len(failed)} of {len(results)} probes", file=sys.stderr)
        sys.exit(1)
    print(f"\ngateway-check passed: {len(results)} probes")


if __name__ == "__main__":
    main()
