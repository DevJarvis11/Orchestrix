# fastembed ships without type stubs.
# pyright: reportMissingTypeStubs=false
"""Embedding and rerank clients (SPEC 13.3, 13.5; amendment A5; plan improvement 6).

Embeddings: Yotta qwen3-embed through our LiteLLM proxy (alias `embed`, 2560-d). There is no
local fallback: vectors from different models cannot share an index, so a failed embedding
leaves the row NULL (backfilled later) and retrieval degrades to keyword search.
Rerank: Yotta qwen3-rerank (alias `rerank`) first, local fastembed ONNX cross-encoder fallback.
"""

import asyncio
import math
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from orchestrix_core.config import Settings
from orchestrix_core.logging import get_logger

log = get_logger(__name__)

LOCAL_RERANK_MODEL = "BAAI/bge-reranker-base"


class EmbeddingUnavailable(Exception):
    pass


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]: ...


class GatewayEmbedder:
    def __init__(
        self, http: httpx.AsyncClient, base_url: str, api_key: str, model: str, dim: int
    ) -> None:
        self._http = http
        self._url = base_url.rstrip("/") + "/v1/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._dim = dim

    @classmethod
    def from_settings(cls, http: httpx.AsyncClient, settings: Settings) -> "GatewayEmbedder":
        return cls(
            http,
            settings.litellm_url,
            settings.litellm_master_key.get_secret_value(),
            settings.embedding_model,
            settings.embedding_dim,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._http.post(
                self._url,
                headers=self._headers,
                json={"model": self._model, "input": list(texts)},
                timeout=30,
            )
            response.raise_for_status()
            data: list[dict[str, Any]] = sorted(
                response.json()["data"], key=lambda d: int(d.get("index", 0))
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EmbeddingUnavailable(f"embedding endpoint failed: {exc}") from exc
        vectors = [[float(x) for x in d["embedding"]] for d in data]
        if any(len(v) != self._dim for v in vectors):  # SPEC 13.5: never mix dimensions
            raise EmbeddingUnavailable(f"embedding dimension is not {self._dim}")
        return vectors


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class LocalReranker:
    """fastembed's ONNX cross-encoder on CPU (no PyTorch). Loaded on first use."""

    def __init__(self, model: str = LOCAL_RERANK_MODEL) -> None:
        self._model_name = model
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        async with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = await asyncio.to_thread(TextCrossEncoder, self._model_name)
        model = self._model
        scores: list[float] = await asyncio.to_thread(
            lambda: [float(s) for s in model.rerank(query, list(documents))]
        )
        return [_sigmoid(s) for s in scores]  # logits -> 0..1, comparable with the http scores


class GatewayReranker:
    """LiteLLM /rerank (Cohere-style). Falls back to `fallback` when the VPN model is down."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str = "rerank",
        fallback: Reranker | None = None,
    ) -> None:
        self._http = http
        self._url = base_url.rstrip("/") + "/rerank"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._fallback = fallback

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        try:
            response = await self._http.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._model,
                    "query": query,
                    "documents": list(documents),
                    "top_n": len(documents),
                },
                timeout=30,
            )
            response.raise_for_status()
            scores = [0.0] * len(documents)
            for item in response.json()["results"]:
                scores[int(item["index"])] = float(item["relevance_score"])
            return scores
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            if self._fallback is None:
                raise
            log.warning("remote rerank failed; using the local reranker", error=str(exc))
            return await self._fallback.rerank(query, documents)
