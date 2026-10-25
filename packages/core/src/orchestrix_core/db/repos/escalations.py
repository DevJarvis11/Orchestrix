"""Escalation rows mirror the graph's pending interrupts (SPEC 14). Upserts keyed by interrupt id."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import Escalation, EscalationAction

DEFAULT_SLA = timedelta(hours=24)


class EscalationRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_open(self, run_id: UUID, interrupt_id: str, payload: dict[str, Any]) -> bool:
        """ON CONFLICT (run_id, interrupt_id) DO NOTHING (SPEC 11.6). True if newly created."""
        async with self._sessions.begin() as s:
            result = await s.execute(
                insert(Escalation)
                .values(
                    run_id=run_id,
                    interrupt_id=interrupt_id,
                    trigger=payload["trigger"],
                    level=payload["level"],
                    payload=payload,
                    status="open",
                    due_at=text(f"now() + interval '{int(DEFAULT_SLA.total_seconds())} seconds'"),
                )
                .on_conflict_do_nothing(index_elements=["run_id", "interrupt_id"])
                .returning(Escalation.id)
            )
            return result.first() is not None

    async def resolve_missing(self, run_id: UUID, pending_interrupt_ids: set[str]) -> None:
        """Reconcile (SPEC 14.3 step 6): open rows whose interrupt is gone were answered."""
        async with self._sessions.begin() as s:
            rows = await s.execute(
                select(Escalation).where(Escalation.run_id == run_id, Escalation.status == "open")
            )
            for esc in rows.scalars():
                if esc.interrupt_id not in pending_interrupt_ids:
                    esc.status = "resolved"

    async def resolved_decisions(self, run_id: UUID) -> dict[str, dict[str, Any]]:
        """Decisions posted for this run (SPEC 14.3 step 5), keyed by interrupt id: latest action wins."""
        async with self._sessions() as s:
            rows = await s.execute(
                select(Escalation.interrupt_id, EscalationAction.body)
                .join(EscalationAction, EscalationAction.escalation_id == Escalation.id)
                .where(Escalation.run_id == run_id, Escalation.status == "resolved")
                .order_by(EscalationAction.created_at)
            )
            return {interrupt_id: dict(body) for interrupt_id, body in rows.all()}
