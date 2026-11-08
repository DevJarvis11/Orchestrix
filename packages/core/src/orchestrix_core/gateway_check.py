"""`make gateway-check`: probe every catalogue model through the LiteLLM proxy (SPEC 16.2, A1).

The chat models come from the proxy's own catalogue (`/model/info`), so adding or swapping a model
in infra/litellm/config.yaml is all it takes. For each chat model: a plain chat, a json_schema
structured output and a tool call. Then an embedding (must be EMBEDDING_DIM wide), a rerank (must
rank the relevant document first) and a forced fallback, unless ORCHESTRIX_LOCAL_FALLBACK=false
(VPN models only). Exits non-zero on any failure.
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

EMBED_MODEL = "embed"
RERANK_MODEL = "rerank"
FALLBACK_PROBE_MODEL = "fallback-probe"  # unreachable deployment that must fall back
LOCAL_PROVIDERS = ("ollama/", "ollama_chat/")  # what --local-only means: host Ollama

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
    response = await client.post("/v1/chat/completions", json={"model": model, **payload})
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
        if attempted < 1 or group in (None, FALLBACK_PROBE_MODEL):
            raise CheckFailed(f"no fallback (attempted={attempted}, group={group})")
        return f"{FALLBACK_PROBE_MODEL} -> {group}"

    return probe


async def chat_models(
    client: httpx.AsyncClient, *, local_only: bool, with_local: bool
) -> list[str]:
    """Chat models in the proxy's catalogue (the probe deployment excluded)."""
    response = await client.get("/model/info")
    response.raise_for_status()
    names: list[str] = []
    entries: list[dict[str, Any]] = response.json().get("data", [])
    for entry in entries:
        name = str(entry.get("model_name", ""))
        params: dict[str, Any] = entry.get("litellm_params") or {}
        info: dict[str, Any] = entry.get("model_info") or {}
        local = str(params.get("model", "")).startswith(LOCAL_PROVIDERS)
        chat = info.get("mode") == "chat"
        wanted = local if local_only else (with_local or not local)
        if chat and wanted and name != FALLBACK_PROBE_MODEL and name not in names:
            names.append(name)
    return names


def build_probes(models: list[str], *, local_only: bool, fallback: bool) -> list[tuple[str, Probe]]:
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
    if fallback:
        probes.append(("forced fallback", fallback_probe()))
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
    parser.add_argument(
        "--local-only", action="store_true", help="only the host-Ollama models (no VPN)"
    )
    args = parser.parse_args()

    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.litellm_master_key.get_secret_value()}"}

    async def go() -> list[Result]:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url,
            headers=headers,
            timeout=360,  # > proxy timeouts
        ) as client:
            local = settings.local_fallback  # false = VPN models only: never probe Ollama
            models = await chat_models(client, local_only=args.local_only, with_local=local)
            return await run(
                client, build_probes(models, local_only=args.local_only, fallback=local)
            )

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
