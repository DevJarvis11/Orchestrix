"""Append-only audit log (FR-92, SPEC 2): every human and admin action, every policy deny."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import AuditLog


class AuditRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def write(
        self,
        *,
        actor_id: UUID | None,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Pass `session` to write inside the caller's transaction (same commit as the change)."""
        stmt = insert(AuditLog).values(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            reason=reason,
        )
        if session is not None:
            await session.execute(stmt)
            return
        async with self._sessions.begin() as s:
            await s.execute(stmt)

    async def list(
        self,
        *,
        limit: int = 200,
        before_id: int | None = None,
        since: datetime | None = None,
        action: str | None = None,
    ) -> list[AuditLog]:
        async with self._sessions() as s:
            q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
            if before_id is not None:
                q = q.where(AuditLog.id < before_id)
            if since is not None:
                q = q.where(AuditLog.created_at >= since)
            if action:
                q = q.where(AuditLog.action == action)
            return list((await s.execute(q)).scalars())
