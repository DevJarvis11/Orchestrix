"""Runs and run leases (SPEC 8.2): at most one worker executes a run, enforced in Postgres."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import PlanRow, Run, SubtaskRow

RunStatus = str  # queued|running|paused|completed|failed|cancelled


@dataclass(frozen=True)
class Reclaimed:
    run_id: UUID
    reclaim_count: int


class RunRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, run_id: UUID) -> Run | None:
        async with self._sessions() as s:
            return await s.get(Run, run_id)

    async def acquire_lease(self, run_id: UUID, owner: str, seconds: int) -> bool:
        """SPEC 8.2 step 1. No row back means another worker holds the run."""
        async with self._sessions.begin() as s:
            row = await s.execute(
                text(
                    "UPDATE runs SET lease_owner = :owner, "
                    "lease_expires_at = now() + make_interval(secs => :secs), "
                    "status = 'running', started_at = coalesce(started_at, now()) "
                    "WHERE id = :run AND status IN ('queued', 'running', 'paused') "
                    "AND (lease_owner IS NULL OR lease_expires_at < now()) RETURNING id"
                ),
                {"owner": owner, "secs": seconds, "run": run_id},
            )
            return row.first() is not None

    async def heartbeat(self, run_id: UUID, owner: str, seconds: int) -> bool:
        """SPEC 8.2 step 2. False means the lease was lost: stop without writing further."""
        async with self._sessions.begin() as s:
            row = await s.execute(
                text(
                    "UPDATE runs SET lease_expires_at = now() + make_interval(secs => :secs) "
                    "WHERE id = :run AND lease_owner = :owner RETURNING id"
                ),
                {"owner": owner, "secs": seconds, "run": run_id},
            )
            return row.first() is not None

    async def release(self, run_id: UUID, owner: str, status: RunStatus) -> bool:
        """SPEC 8.2 step 3: on pause, completion, failure or cancel."""
        terminal = status in ("completed", "failed", "cancelled")
        async with self._sessions.begin() as s:
            row = await s.execute(
                text(
                    "UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL, status = :status, "
                    "ended_at = CASE WHEN :terminal THEN now() ELSE ended_at END "
                    "WHERE id = :run AND lease_owner = :owner RETURNING id"
                ),
                {"owner": owner, "status": status, "terminal": terminal, "run": run_id},
            )
            return row.first() is not None

    async def reap_expired(self) -> list[Reclaimed]:
        """SPEC 8.2 step 4: clear expired leases of running runs and count the reclaim."""
        async with self._sessions.begin() as s:
            rows = await s.execute(
                text(
                    "UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL, "
                    "reclaim_count = reclaim_count + 1 "
                    "WHERE status = 'running' AND lease_expires_at < now() "
                    "RETURNING id, reclaim_count"
                )
            )
            return [Reclaimed(r.id, r.reclaim_count) for r in rows]

    async def set_status(self, run_id: UUID, status: RunStatus) -> None:
        terminal = status in ("completed", "failed", "cancelled")
        async with self._sessions.begin() as s:
            values: dict[str, Any] = {
                "status": status,
                "lease_owner": None,
                "lease_expires_at": None,
            }
            if terminal:
                values["ended_at"] = text("now()")
            await s.execute(update(Run).where(Run.id == run_id).values(**values))

    async def set_model_config(self, run_id: UUID, models: dict[str, str]) -> dict[str, str]:
        """Snapshot the resolved models once (A3); later calls return the stored snapshot."""
        async with self._sessions.begin() as s:
            run = await s.get(Run, run_id, with_for_update=True)
            if run is None:
                raise LookupError(run_id)
            if run.model_config_ is None:
                run.model_config_ = dict(models)
            return dict(run.model_config_)

    async def set_head_checkpoint(self, run_id: UUID, checkpoint_id: str) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                update(Run).where(Run.id == run_id).values(head_checkpoint_id=checkpoint_id)
            )

    async def for_task(self, task_id: UUID) -> Run | None:
        async with self._sessions() as s:
            result = await s.execute(
                select(Run)
                .where(Run.task_id == task_id, Run.kind == "main")
                .order_by(Run.created_at)
            )
            return result.scalars().first()

    async def latest_plan(self, run_id: UUID) -> PlanRow | None:
        async with self._sessions() as s:
            result = await s.execute(
                select(PlanRow)
                .where(PlanRow.run_id == run_id)
                .order_by(PlanRow.version.desc())
                .limit(1)
            )
            return result.scalars().first()

    async def subtasks(self, run_id: UUID, plan_version: int) -> list[SubtaskRow]:
        async with self._sessions() as s:
            result = await s.execute(
                select(SubtaskRow)
                .where(SubtaskRow.run_id == run_id, SubtaskRow.plan_version == plan_version)
                .order_by(SubtaskRow.key)
            )
            return list(result.scalars())
