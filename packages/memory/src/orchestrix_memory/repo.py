"""memories table access (SPEC 13). Every query uses bound parameters.

Retrieval is the SPEC 13.3 Reciprocal Rank Fusion with two deviations (BUILD_LOG):
- the vector arm only reads embedded rows (A5: rows written while the VPN is down are NULL),
- the keyword arm ORs the task's lexemes: `websearch_to_tsquery` ANDs every word of a long
  task text and almost never matches, which would leave the keyword arm empty.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Text, bindparam, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import EMBEDDING_DIM, AppMeta, Memory, Task
from orchestrix_memory.scoring import CANDIDATES, DEDUPE_COSINE

_NS = uuid.UUID("0b3e7f62-8a3c-4d21-9a8e-3f4c2a1b5d70")
EMBEDDING_META_KEY = "embedding_model"

_VEC_ARM = """
  SELECT id, row_number() OVER (ORDER BY embedding <=> :q_vec) AS r
  FROM memories
  WHERE namespace = ANY(:ns) AND consolidated_into IS NULL AND embedding IS NOT NULL
    AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(:kinds))
    AND (CAST(:since AS timestamptz) IS NULL OR created_at >= :since)
  ORDER BY embedding <=> :q_vec LIMIT 30"""
_NO_VEC_ARM = "SELECT NULL::uuid AS id, 0::bigint AS r WHERE false"
_FUSION = """
WITH vec AS ({vec}
), txt AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
  FROM memories,
       to_tsquery('english', replace(plainto_tsquery('english', :q_txt)::text, ' & ', ' | ')) q
  WHERE namespace = ANY(:ns) AND consolidated_into IS NULL AND tsv @@ q
    AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(:kinds))
    AND (CAST(:since AS timestamptz) IS NULL OR created_at >= :since)
  ORDER BY ts_rank_cd(tsv, q) DESC LIMIT 30
)
SELECT m.id, m.kind, m.text, m.importance, m.created_at, m.last_used_at, f.rrf
FROM (
  SELECT id, sum(1.0 / (60 + r)) AS rrf             -- Reciprocal Rank Fusion, k = 60
  FROM (SELECT * FROM vec UNION ALL SELECT * FROM txt) u
  GROUP BY id
) f JOIN memories m ON m.id = f.id
ORDER BY f.rrf DESC
LIMIT :limit
"""
_TEXT_PARAMS = (bindparam("ns", type_=ARRAY(Text)), bindparam("kinds", type_=ARRAY(Text)))
HYBRID_SQL = text(_FUSION.format(vec=_VEC_ARM)).bindparams(
    bindparam("q_vec", type_=HALFVEC(EMBEDDING_DIM)), *_TEXT_PARAMS
)
KEYWORD_SQL = text(_FUSION.format(vec=_NO_VEC_ARM)).bindparams(*_TEXT_PARAMS)


@dataclass(frozen=True)
class Candidate:
    id: UUID
    kind: str
    text: str
    importance: float
    created_at: datetime
    last_used_at: datetime | None
    rrf: float


def user_ns(user_id: str | UUID) -> str:
    return f"user:{user_id}"


def team_ns(team_id: str | UUID) -> str:
    return f"team:{team_id}"


def memory_id(run_id: str, kind: str, text_: str) -> UUID:
    """Deterministic per run: a re-run write_memory node inserts nothing new (SPEC 11.6)."""
    return uuid.uuid5(_NS, f"{run_id}|{kind}|{' '.join(text_.lower().split())}")


class MemoryRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # --- retrieval ------------------------------------------------------------------------

    async def hybrid(
        self,
        *,
        namespaces: list[str],
        q_text: str,
        q_vec: list[float] | None,
        kinds: list[str] | None = None,
        since: datetime | None = None,
        limit: int = CANDIDATES,
    ) -> list[Candidate]:
        params: dict[str, Any] = {
            "ns": namespaces,
            "q_txt": q_text,
            "kinds": kinds,
            "since": since,
            "limit": limit,
        }
        if q_vec is not None:
            stmt, params["q_vec"] = HYBRID_SQL, q_vec
        else:
            stmt = KEYWORD_SQL
        async with self._sessions() as s:
            rows = (await s.execute(stmt, params)).all()
        return [Candidate(*r) for r in rows]

    async def mark_used(self, ids: list[UUID]) -> None:
        """FR-53: used memories resist decay."""
        if not ids:
            return
        async with self._sessions.begin() as s:
            await s.execute(
                update(Memory)
                .where(Memory.id.in_(ids))
                .values(access_count=Memory.access_count + 1, last_used_at=func.now())
            )

    # --- writes ---------------------------------------------------------------------------

    async def near_duplicate(self, namespace: str, vector: list[float]) -> UUID | None:
        """An active memory in the namespace with cosine >= 0.92 (SPEC 13.2 step 4)."""
        stmt = (
            select(Memory.id)
            .where(
                Memory.namespace == namespace,
                Memory.consolidated_into.is_(None),
                Memory.embedding.is_not(None),
                Memory.embedding.cosine_distance(vector) <= 1 - DEDUPE_COSINE,
            )
            .order_by(Memory.embedding.cosine_distance(vector))
            .limit(1)
        )
        async with self._sessions() as s:
            return (await s.execute(stmt)).scalar_one_or_none()

    async def same_text(self, namespace: str, text_: str) -> UUID | None:
        """Exact-text dedupe when no embedding is available (VPN down)."""
        normal = " ".join(text_.lower().split())
        stmt = select(Memory.id).where(
            Memory.namespace == namespace,
            Memory.consolidated_into.is_(None),
            func.lower(func.regexp_replace(Memory.text, r"\s+", " ", "g")) == normal,
        )
        async with self._sessions() as s:
            return (await s.execute(stmt.limit(1))).scalar_one_or_none()

    async def reinforce(self, memory: UUID, importance: float) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                update(Memory)
                .where(Memory.id == memory)
                .values(
                    importance=func.greatest(Memory.importance, importance), last_used_at=func.now()
                )
            )

    async def insert(
        self,
        *,
        id_: UUID,
        namespace: str,
        kind: str,
        text_: str,
        importance: float,
        embedding: list[float] | None,
        source_run_id: UUID | None,
        source_ref: str | None,
        derived_from: list[UUID] | None = None,
    ) -> bool:
        async with self._sessions.begin() as s:
            row = await s.execute(
                insert(Memory)
                .values(
                    id=id_,
                    namespace=namespace,
                    kind=kind,
                    text=text_,
                    importance=importance,
                    embedding=embedding,
                    source_run_id=source_run_id,
                    source_ref=source_ref,
                    derived_from=derived_from or [],
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(Memory.id)
            )
            return row.first() is not None

    # --- user-facing CRUD (FR-55) ---------------------------------------------------------

    async def list_for(
        self,
        namespaces: list[str],
        *,
        kind: str | None = None,
        q: str | None = None,
        include_consolidated: bool = False,
        limit: int = 200,
    ) -> list[Memory]:
        stmt = select(Memory).where(Memory.namespace.in_(namespaces))
        if not include_consolidated:
            stmt = stmt.where(Memory.consolidated_into.is_(None))
        if kind:
            stmt = stmt.where(Memory.kind == kind)
        if q:
            stmt = stmt.where(
                or_(
                    Memory.tsv.op("@@")(func.websearch_to_tsquery("english", q)),
                    Memory.text.ilike(f"%{q}%"),
                )
            )
        async with self._sessions() as s:
            return list(
                (await s.execute(stmt.order_by(Memory.created_at.desc()).limit(limit))).scalars()
            )

    async def get(self, memory: UUID) -> Memory | None:
        async with self._sessions() as s:
            return await s.get(Memory, memory)

    async def update_text(
        self,
        memory: UUID,
        *,
        text_: str | None,
        importance: float | None,
        embedding: list[float] | None,
    ) -> Memory | None:
        async with self._sessions.begin() as s:
            row = await s.get(Memory, memory, with_for_update=True)
            if row is None:
                return None
            if text_ is not None:
                row.text = text_
                row.embedding = embedding  # re-embedded, or NULL until the backfill job runs
            if importance is not None:
                row.importance = importance
            await s.flush()
            await s.refresh(row)
            return row

    async def delete_cascade(self, memory: UUID) -> list[UUID]:
        """FR-55, 13.4: hard delete, plus every summary derived (transitively) from it, so deleted
        information cannot survive inside a consolidation. Returns the deleted ids."""
        deleted: list[UUID] = []
        frontier = [memory]
        async with self._sessions.begin() as s:
            while frontier:
                derived = list(
                    (
                        await s.execute(
                            select(Memory.id).where(Memory.derived_from.overlap(frontier))
                        )
                    ).scalars()
                )
                deleted += frontier
                frontier = [d for d in derived if d not in deleted]
            await s.execute(delete(Memory).where(Memory.id.in_(deleted)))
        return deleted

    async def erase_user(self, user_id: UUID) -> int:
        """Full user deletion (13.4): their memories, summaries derived from them, and the user's
        own text in their tasks."""
        async with self._sessions() as s:
            ids = list(
                (
                    await s.execute(select(Memory.id).where(Memory.namespace == user_ns(user_id)))
                ).scalars()
            )
        count = 0
        for memory in ids:
            count += len(await self.delete_cascade(memory))
        async with self._sessions.begin() as s:
            await s.execute(
                update(Task)
                .where(Task.requester_id == user_id)
                .values(title="[erased]", description="[erased at the user's request]")
            )
        return count

    # --- maintenance ----------------------------------------------------------------------

    async def unembedded(self, limit: int = 64) -> list[Memory]:
        async with self._sessions() as s:
            stmt = (
                select(Memory)
                .where(Memory.embedding.is_(None))
                .order_by(Memory.created_at)
                .limit(limit)
            )
            return list((await s.execute(stmt)).scalars())

    async def set_embedding(self, memory: UUID, vector: list[float]) -> None:
        async with self._sessions.begin() as s:
            await s.execute(update(Memory).where(Memory.id == memory).values(embedding=vector))

    async def similar_pairs(self) -> list[tuple[UUID, UUID]]:
        """Consolidation input (FR-54): active pairs of one kind in one namespace, cosine >= 0.92."""
        stmt = text(
            """
SELECT a.id, b.id FROM memories a
JOIN memories b ON a.namespace = b.namespace AND a.kind = b.kind AND a.id < b.id
WHERE a.consolidated_into IS NULL AND b.consolidated_into IS NULL
  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
  AND (a.embedding <=> b.embedding) <= :max_distance
"""
        )
        async with self._sessions() as s:
            rows = (await s.execute(stmt, {"max_distance": 1 - DEDUPE_COSINE})).all()
        return [(r[0], r[1]) for r in rows]

    async def by_ids(self, ids: list[UUID]) -> list[Memory]:
        async with self._sessions() as s:
            return list((await s.execute(select(Memory).where(Memory.id.in_(ids)))).scalars())

    async def mark_consolidated(self, originals: list[UUID], summary: UUID) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                update(Memory).where(Memory.id.in_(originals)).values(consolidated_into=summary)
            )

    async def check_embedding_model(self, model: str, dim: int) -> None:
        """SPEC 13.5: the worker refuses to start if the stored embedding model differs."""
        want = {"model": model, "dim": dim}
        async with self._sessions.begin() as s:
            row = await s.get(AppMeta, EMBEDDING_META_KEY)
            if row is None:
                s.add(AppMeta(key=EMBEDDING_META_KEY, value=want))
                return
            if dict(row.value) != want:
                raise RuntimeError(
                    f"embedding model mismatch: the database holds {row.value}, settings say {want}. "
                    "Re-embed with a migration (SPEC 13.5) before switching models."
                )
