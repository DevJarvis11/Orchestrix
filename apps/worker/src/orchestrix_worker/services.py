"""Production Services for the graph: SQL recorder writing app tables idempotently (SPEC 11.6)."""

import hashlib
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import Artifact, PlanRow, ReviewRow, SubtaskRow, Task
from orchestrix_core.db.repos.tasks import TaskRepo
from orchestrix_core.schemas import Plan, PlannedSubtask, Review, SubtaskResult, TaskStatus
from orchestrix_core.storage import ObjectStore

# Deterministic ids make re-run inserts collide instead of duplicating rows.
_NS = uuid.UUID("6f1b8c1e-4d6a-4f0e-9a51-0b7c3e2d9a10")


def stable_id(*parts: object) -> UUID:
    return uuid.uuid5(_NS, ":".join(str(p) for p in parts))


def _uuids(values: list[str]) -> list[UUID]:
    out: list[UUID] = []
    for v in values:
        try:
            out.append(UUID(v))
        except ValueError:
            continue  # planner cited something that is not a memory id
    return out


class SqlRunRecorder:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], tasks: TaskRepo, store: ObjectStore
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._store = store

    async def set_task_status(
        self, task_id: str, status: TaskStatus, reason: str | None = None
    ) -> None:
        await self._tasks.set_status(UUID(task_id), status, reason)

    async def save_plan(self, run_id: str, version: int, plan: Plan, edited_by: str | None) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                insert(PlanRow)
                .values(
                    run_id=UUID(run_id),
                    version=version,
                    body=plan.model_dump(mode="json"),
                    confidence=plan.confidence,
                    rationale=plan.confidence_reason,
                    memory_ids=_uuids(plan.used_memory_ids),
                    edited_by=next(iter(_uuids([edited_by] if edited_by else [])), None),
                )
                .on_conflict_do_nothing(index_elements=["run_id", "version"])
            )

    async def save_subtask(
        self,
        run_id: str,
        plan_version: int,
        subtask: PlannedSubtask,
        status: str,
        *,
        attempts: int = 0,
        revisions: int = 0,
        result: SubtaskResult | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "attempts": attempts,
            "revisions": revisions,
        }
        if result is not None:
            values["result"] = result.model_dump(mode="json")
        async with self._sessions.begin() as s:
            await s.execute(
                insert(SubtaskRow)
                .values(
                    run_id=UUID(run_id),
                    plan_version=plan_version,
                    key=subtask.key,
                    specialist=subtask.specialist,
                    **values,
                )
                .on_conflict_do_update(
                    index_elements=["run_id", "plan_version", "key"], set_=values
                )
            )

    async def save_review(
        self, run_id: str, subtask_key: str | None, ordinal: int, review: Review, model: str
    ) -> None:
        async with self._sessions.begin() as s:
            subtask_id = None
            if subtask_key is not None:
                subtask_id = (
                    await s.execute(
                        select(SubtaskRow.id)
                        .where(SubtaskRow.run_id == UUID(run_id), SubtaskRow.key == subtask_key)
                        .order_by(SubtaskRow.plan_version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            await s.execute(
                insert(ReviewRow)
                .values(
                    id=stable_id(run_id, "review", subtask_key or "final", ordinal),
                    run_id=UUID(run_id),
                    subtask_id=subtask_id,
                    verdict=review.verdict,
                    score=review.score,
                    issues=[i.model_dump(mode="json") for i in review.issues],
                    feedback=review.feedback,
                    reviewer_model=model,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )

    async def save_deliverable(self, run_id: str, task_id: str, content: str) -> str | None:
        body = content.encode()
        key = f"runs/{run_id}/deliverable.md"
        await self._store.put(key, body, "text/markdown")
        artifact_id = stable_id(run_id, "deliverable")
        async with self._sessions.begin() as s:
            await s.execute(
                insert(Artifact)
                .values(
                    id=artifact_id,
                    run_id=UUID(run_id),
                    task_id=UUID(task_id),
                    name="deliverable.md",
                    mime="text/markdown",
                    bytes=len(body),
                    object_key=key,
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await s.execute(
                update(Task).where(Task.id == UUID(task_id)).values(final_artifact_id=artifact_id)
            )
        return str(artifact_id)
