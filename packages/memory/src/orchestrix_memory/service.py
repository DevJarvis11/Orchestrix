"""MemoryService (SPEC 13): the MemoryStore the graph uses, plus the scheduler's maintenance jobs."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from orchestrix_agents.context import ChatProvider, RunDigest
from orchestrix_agents.llm import StructuredOutputError, invoke_structured
from orchestrix_agents.prompting import render_prompt
from orchestrix_core.logging import get_logger
from orchestrix_core.pii import Redactor
from orchestrix_core.schemas import CallTag, MemoryHit, TaskInput
from orchestrix_core.telemetry import span
from orchestrix_memory.clients import Embedder, EmbeddingUnavailable, Reranker
from orchestrix_memory.repo import MemoryRepo, memory_id, team_ns, user_ns
from orchestrix_memory.scoring import TOP_K, final_score

log = get_logger(__name__)

Kind = Literal["episode", "lesson", "preference", "fact"]
QUERY_CHARS = 2000


class MemoryCandidate(BaseModel):
    """SPEC 13.2 step 1."""

    kind: Kind
    text: str = Field(min_length=1, max_length=500)
    importance: float = Field(ge=0, le=1)
    source_ref: str | None = None  # facts: a source the run cited
    quote: str | None = None  # preferences: the requester's exact words


class MemoryCandidates(BaseModel):
    memories: list[MemoryCandidate] = Field(default=[], max_length=10)


class Consolidated(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def query_text(task: TaskInput) -> str:
    """Title plus a condensed description (plan improvement 2)."""
    return f"{task.title}\n{' '.join(task.description.split())[:QUERY_CHARS]}"


def namespace_for(kind: str, user_id: str, team_id: str) -> str:
    """SPEC 13.2 step 5: preferences are personal; episodes, lessons and facts are the team's."""
    return user_ns(user_id) if kind == "preference" else team_ns(team_id)


def validate(candidate: MemoryCandidate, digest: RunDigest) -> str | None:
    """FR-56. Returns why a candidate is invalid, or None."""
    if candidate.kind == "fact" and (
        not candidate.source_ref or candidate.source_ref not in set(digest.sources)
    ):
        return "fact without a source the run cited"
    if candidate.kind == "preference":
        quote = (candidate.quote or "").strip()
        if not quote or not any(quote in words for words in digest.user_words):
            return "preference not quoted from the requester's own words"
    return None


class MemoryService:
    def __init__(
        self, repo: MemoryRepo, embedder: Embedder, reranker: Reranker, redactor: Redactor
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._reranker = reranker
        self._redactor = redactor

    async def _embed_one(self, text: str) -> list[float] | None:
        try:
            return (await self._embedder.embed([text]))[0]
        except EmbeddingUnavailable as exc:
            log.warning("embedding unavailable; continuing without a vector", error=str(exc))
            return None

    # --- retrieval (FR-52, FR-53, SPEC 13.3) --------------------------------------------------

    async def retrieve(
        self, *, run_id: str, task: TaskInput, user_id: str, team_id: str
    ) -> list[MemoryHit]:
        query = query_text(task)
        with span("memory.retrieve", **{"orchestrix.run_id": run_id}) as sp:
            vector = await self._embed_one(query)
            candidates = await self._repo.hybrid(
                namespaces=[user_ns(user_id), team_ns(team_id)], q_text=query, q_vec=vector
            )
            if not candidates:
                return []
            try:
                rerank = await self._reranker.rerank(query, [c.text for c in candidates])
            except Exception as exc:  # no reranker at all: fall back to the fusion order
                log.warning("rerank failed; using RRF order", error=str(exc))
                top = max(c.rrf for c in candidates)
                rerank = [c.rrf / top for c in candidates]
            now = datetime.now(UTC)
            scored = sorted(
                (
                    (final_score(r, c.importance, c.last_used_at or c.created_at, now), c)
                    for r, c in zip(rerank, candidates, strict=True)
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )[:TOP_K]
            if sp is not None:
                sp.set_attribute("orchestrix.memory.hits", len(scored))
                sp.set_attribute("orchestrix.memory.keyword_only", vector is None)
        return [
            MemoryHit(id=str(c.id), kind=c.kind, text=c.text, score=round(max(0.0, s), 6))  # type: ignore[arg-type]
            for s, c in scored
        ]

    async def mark_used(self, memory_ids: list[str]) -> None:
        ids: list[UUID] = []
        for raw in memory_ids:
            try:
                ids.append(UUID(raw))
            except ValueError:
                continue  # the planner cited something that is not a memory id
        await self._repo.mark_used(ids)

    # --- writing (FR-51, FR-56, FR-93, SPEC 13.2) ---------------------------------------------

    async def write(
        self, *, run_id: str, user_id: str, team_id: str, digest: RunDigest, llm: ChatProvider
    ) -> list[str]:
        with span("memory.write", **{"orchestrix.run_id": run_id}):
            prompt = render_prompt(
                "memory_writer.j2",
                task=digest.task,
                outcome=digest.outcome,
                plan=digest.plan,
                reviews=digest.reviews,
                errors=digest.errors,
                escalations=digest.escalations,
                sources=sorted(set(digest.sources))[:60],
                user_words=digest.user_words,
            )
            try:
                result = await invoke_structured(
                    llm.chat("fast", CallTag(agent="memory_writer", purpose="memory")),
                    MemoryCandidates,
                    [HumanMessage(prompt)],
                    price=llm.price("fast"),
                )
            except StructuredOutputError as exc:
                log.warning("memory writer produced no valid candidates", error=str(exc))
                return []
            written: list[str] = []
            for candidate in result.value.memories[:10]:
                reason = validate(candidate, digest)
                if reason is not None:
                    log.info("memory candidate dropped", kind=candidate.kind, reason=reason)
                    continue
                stored = await self._store(run_id, user_id, team_id, candidate)
                if stored is not None:
                    written.append(stored)
            return written

    async def _store(
        self, run_id: str, user_id: str, team_id: str, candidate: MemoryCandidate
    ) -> str | None:
        text = self._redactor.text(candidate.text)
        namespace = namespace_for(candidate.kind, user_id, team_id)
        vector = await self._embed_one(text)
        duplicate = (
            await self._repo.near_duplicate(namespace, vector)
            if vector is not None
            else await self._repo.same_text(namespace, text)
        )
        if duplicate is not None:  # SPEC 13.2 step 4: strengthen the existing memory instead
            await self._repo.reinforce(duplicate, candidate.importance)
            return None
        new_id = memory_id(run_id, candidate.kind, text)
        created = await self._repo.insert(
            id_=new_id,
            namespace=namespace,
            kind=candidate.kind,
            text_=text,
            importance=candidate.importance,
            embedding=vector,
            source_run_id=UUID(run_id),
            source_ref=self._redactor.text(candidate.source_ref or candidate.quote or "") or None,
        )
        return str(new_id) if created else None

    # --- maintenance (FR-54, A5 backfill) -----------------------------------------------------

    async def backfill(self, limit: int = 64) -> int:
        """Embed rows written while the embedding endpoint was down."""
        rows = await self._repo.unembedded(limit)
        if not rows:
            return 0
        try:
            vectors = await self._embedder.embed([r.text for r in rows])
        except EmbeddingUnavailable:
            return 0
        for row, vector in zip(rows, vectors, strict=True):
            await self._repo.set_embedding(row.id, vector)
        return len(rows)

    async def consolidate(self, llm: ChatProvider) -> int:
        """Nightly (FR-54): merge groups of near-duplicates; originals stay for audit."""
        pairs = await self._repo.similar_pairs()
        parent: dict[UUID, UUID] = {}

        def find(x: UUID) -> UUID:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in pairs:
            parent[find(a)] = find(b)
        groups: dict[UUID, list[UUID]] = {}
        for node in list(parent):
            groups.setdefault(find(node), []).append(node)
        merged = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            rows = await self._repo.by_ids(members)
            if len({(r.namespace, r.kind) for r in rows}) != 1:
                continue
            first = rows[0]
            try:
                summary = await invoke_structured(
                    llm.chat("fast", CallTag(agent="memory_writer", purpose="memory")),
                    Consolidated,
                    [
                        HumanMessage(
                            render_prompt("memory_consolidate.j2", kind=first.kind, memories=rows)
                        )
                    ],
                    price=llm.price("fast"),
                )
            except StructuredOutputError:
                continue
            text = self._redactor.text(summary.value.text)
            new_id = memory_id(
                "consolidation:" + ",".join(sorted(str(m) for m in members)), first.kind, text
            )
            await self._repo.insert(
                id_=new_id,
                namespace=first.namespace,
                kind=first.kind,
                text_=text,
                importance=max(r.importance for r in rows),
                embedding=await self._embed_one(text),
                source_run_id=None,
                source_ref=None,
                derived_from=[r.id for r in rows],
            )
            await self._repo.mark_consolidated([r.id for r in rows], new_id)
            merged += 1
        return merged
