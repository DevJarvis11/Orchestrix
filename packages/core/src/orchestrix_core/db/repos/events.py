"""run_events: the durable event log and transactional outbox (SPEC 8.3, 17.2)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import RunEvent


@dataclass(frozen=True)
class StoredEvent:
    id: int
    run_id: UUID
    type: str
    data: dict[str, Any]


class EventRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def insert(self, run_id: UUID, data: dict[str, Any]) -> int:
        async with self._sessions.begin() as s:
            result = await s.execute(
                insert(RunEvent)
                .values(run_id=run_id, type=data["type"], data=data)
                .returning(RunEvent.id)
            )
            return int(result.scalar_one())

    async def mark_published(self, ids: list[int]) -> None:
        if not ids:
            return
        async with self._sessions.begin() as s:
            await s.execute(update(RunEvent).where(RunEvent.id.in_(ids)).values(published=True))

    async def after(self, run_id: UUID, last_id: int, limit: int = 500) -> list[StoredEvent]:
        async with self._sessions() as s:
            rows = await s.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.id > last_id)
                .order_by(RunEvent.id)
                .limit(limit)
            )
            return [StoredEvent(e.id, e.run_id, e.type, e.data) for e in rows.scalars()]

    async def unpublished(self, older_than_seconds: int = 5, limit: int = 500) -> list[StoredEvent]:
        async with self._sessions() as s:
            rows = await s.execute(
                select(RunEvent)
                .where(RunEvent.published.is_(False))
                .where(
                    RunEvent.created_at
                    < text(f"now() - interval '{int(older_than_seconds)} seconds'")
                )
                .order_by(RunEvent.id)
                .limit(limit)
            )
            return [StoredEvent(e.id, e.run_id, e.type, e.data) for e in rows.scalars()]
