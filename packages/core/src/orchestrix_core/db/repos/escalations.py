"""Escalation rows mirror the graph's pending interrupts (SPEC 14). Upserts keyed by interrupt id."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import (
    Escalation,
    EscalationAction,
    EscalationMessage,
    Run,
    Task,
)

DEFAULT_SLA = timedelta(hours=24)


class AlreadyResolved(Exception):
    """The escalation was decided already (HTTP 409)."""


@dataclass(frozen=True)
class EscalationView:
    escalation: Escalation
    task_id: UUID
    task_title: str
    requester_id: UUID
    team_id: UUID


def _due(sla: timedelta) -> Any:
    return text(f"now() + interval '{int(sla.total_seconds())} seconds'")


class EscalationRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_open(
        self,
        run_id: UUID,
        interrupt_id: str,
        payload: dict[str, Any],
        *,
        assignee_team_id: UUID | None = None,
        sla: timedelta = DEFAULT_SLA,
    ) -> UUID | None:
        """ON CONFLICT (run_id, interrupt_id) DO NOTHING (SPEC 11.6). The id if newly created."""
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
                    assignee_team_id=assignee_team_id,
                    due_at=_due(sla),
                )
                .on_conflict_do_nothing(index_elements=["run_id", "interrupt_id"])
                .returning(Escalation.id)
            )
            return result.scalar_one_or_none()

    async def record_notify(
        self, run_id: UUID, key: str, payload: dict[str, Any], assignee_team_id: UUID | None
    ) -> UUID | None:
        """`notify` never pauses (SPEC 14.1): a row that is resolved from the start."""
        async with self._sessions.begin() as s:
            result = await s.execute(
                insert(Escalation)
                .values(
                    run_id=run_id,
                    interrupt_id=f"notify:{key}",
                    trigger=payload["trigger"],
                    level="notify",
                    payload=payload,
                    status="resolved",
                    assignee_team_id=assignee_team_id,
                    due_at=text("now()"),
                )
                .on_conflict_do_nothing(index_elements=["run_id", "interrupt_id"])
                .returning(Escalation.id)
            )
            return result.scalar_one_or_none()

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

    def _view_query(self) -> Any:
        return (
            select(Escalation, Task.id, Task.title, Task.requester_id, Task.team_id)
            .join(Run, Run.id == Escalation.run_id)
            .join(Task, Task.id == Run.task_id)
        )

    async def get(self, escalation_id: UUID) -> EscalationView | None:
        async with self._sessions() as s:
            row = (
                await s.execute(self._view_query().where(Escalation.id == escalation_id))
            ).first()
        return EscalationView(*row) if row else None

    async def inbox(
        self,
        *,
        user_id: UUID,
        team_ids: list[UUID],
        reviewer: bool,
        status: str | None = "open",
        level: str | None = None,
        limit: int = 100,
    ) -> list[EscalationView]:
        """Reviewers see their teams' escalations; requesters see clarify questions on own tasks."""
        visible = [(Escalation.level == "clarify") & (Task.requester_id == user_id)]
        if reviewer and team_ids:
            visible.append(
                (Escalation.level != "clarify") & Escalation.assignee_team_id.in_(team_ids)
            )
        q = self._view_query().where(or_(*visible)).order_by(Escalation.created_at.desc())
        if status:
            q = q.where(Escalation.status == status)
        if level:
            q = q.where(Escalation.level == level)
        async with self._sessions() as s:
            rows = (await s.execute(q.limit(limit))).all()
        return [EscalationView(*r) for r in rows]

    async def actions(self, escalation_id: UUID) -> list[EscalationAction]:
        async with self._sessions() as s:
            q = (
                select(EscalationAction)
                .where(EscalationAction.escalation_id == escalation_id)
                .order_by(EscalationAction.created_at)
            )
            return list((await s.execute(q)).scalars())

    async def decide(
        self, escalation_id: UUID, actor_id: UUID, action: str, body: dict[str, Any]
    ) -> tuple[Escalation, AsyncSession]:
        """Lock, check open, record the action, resolve. Returns the row and the open session
        so the caller writes the audit row in the same transaction; it must commit or roll back.
        """
        s = self._sessions()
        await s.begin()
        esc = await s.get(Escalation, escalation_id, with_for_update=True)
        if esc is None:
            await s.rollback()
            await s.close()
            raise LookupError(escalation_id)
        if esc.status != "open":
            await s.rollback()
            await s.close()
            raise AlreadyResolved(str(escalation_id))
        s.add(EscalationAction(escalation_id=esc.id, actor_id=actor_id, action=action, body=body))
        esc.status = "resolved"
        await s.flush()
        return esc, s

    async def messages(self, escalation_id: UUID) -> list[EscalationMessage]:
        async with self._sessions() as s:
            q = (
                select(EscalationMessage)
                .where(EscalationMessage.escalation_id == escalation_id)
                .order_by(EscalationMessage.created_at)
            )
            return list((await s.execute(q)).scalars())

    async def add_message(
        self, escalation_id: UUID, author_id: UUID | None, role: str, body: str
    ) -> None:
        async with self._sessions.begin() as s:
            s.add(
                EscalationMessage(
                    escalation_id=escalation_id, author_id=author_id, role=role, body=body
                )
            )

    async def overdue(self, sla: timedelta) -> list[Escalation]:
        """SLA (14.5): open rows past due_at. Pushes due_at forward so each is re-notified once
        per SLA window."""
        async with self._sessions.begin() as s:
            rows = await s.execute(
                update(Escalation)
                .where(Escalation.status == "open", Escalation.due_at < text("now()"))
                .values(due_at=_due(sla))
                .returning(Escalation)
            )
            return list(rows.scalars())

    async def expire(self, older_than: timedelta) -> list[Escalation]:
        """Open rows older than the timeout become `expired`; the caller fails their tasks."""
        async with self._sessions.begin() as s:
            rows = await s.execute(
                update(Escalation)
                .where(
                    Escalation.status == "open",
                    Escalation.created_at
                    < text(f"now() - interval '{int(older_than.total_seconds())} seconds'"),
                )
                .values(status="expired")
                .returning(Escalation)
            )
            return list(rows.scalars())
