"""Tasks API (SPEC 17.1): create (FR-02, FR-03, FR-05), read, cancel (FR-04)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from orchestrix_api.deps import Resources, User, require_task_access
from orchestrix_core.cancel import request_cancel
from orchestrix_core.queue import START_RUN, enqueue
from orchestrix_core.schemas import Plan, TaskCreate, TaskStatus
from orchestrix_core.schemas.events import RunLifecycleEvent
from orchestrix_core.state_machine import TERMINAL

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


class TaskAccepted(BaseModel):
    task_id: UUID
    run_id: UUID


class SubtaskView(BaseModel):
    key: str
    specialist: str
    status: str
    attempts: int
    revisions: int


class TaskDetail(BaseModel):
    id: UUID
    title: str
    description: str
    status: TaskStatus
    failure_reason: str | None
    require_plan_approval: bool
    model_overrides: dict[str, Any]
    run_id: UUID | None
    plan: Plan | None
    plan_version: int | None
    subtasks: list[SubtaskView]
    deliverable_artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime


class CancelAccepted(BaseModel):
    task_id: UUID
    status: TaskStatus


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    body: TaskCreate,
    res: Resources,
    user: User,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> TaskAccepted:
    team = await res.users.first_team(user.id)
    if team is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you are not a member of any team")
    budget = body.budget or res.settings.default_budget
    if body.budget is not None and body.budget.max_usd > float(team.monthly_budget_usd):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "budget override exceeds the team cap"
        )
    created = await res.tasks.create(
        requester_id=user.id,
        team_id=team.id,
        body=body,
        budget=budget,
        idempotency_key=idempotency_key,
        graph_version=res.settings.graph_version,
    )
    if created.created:
        await enqueue(START_RUN, str(created.run_id))
    return TaskAccepted(task_id=created.task_id, run_id=created.run_id)


@router.get("/{task_id}")
async def get_task(task_id: UUID, res: Resources, user: User) -> TaskDetail:
    task = await require_task_access(res, user, task_id)
    run = await res.runs.for_task(task.id)
    plan_row = await res.runs.latest_plan(run.id) if run else None
    subtasks = await res.runs.subtasks(run.id, plan_row.version) if run and plan_row else []
    return TaskDetail(
        id=task.id,
        title=task.title,
        description=task.description,
        status=task.status,  # type: ignore[arg-type]
        failure_reason=task.failure_reason,
        require_plan_approval=task.require_plan_approval,
        model_overrides=task.model_overrides,
        run_id=run.id if run else None,
        plan=Plan.model_validate(plan_row.body) if plan_row else None,
        plan_version=plan_row.version if plan_row else None,
        subtasks=[
            SubtaskView(
                key=s.key,
                specialist=s.specialist,
                status=s.status,
                attempts=s.attempts,
                revisions=s.revisions,
            )
            for s in subtasks
        ],
        deliverable_artifact_id=task.final_artifact_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_task(task_id: UUID, res: Resources, user: User) -> CancelAccepted:
    """FR-04: a running run stops at its next dispatch; a queued or paused run stops now."""
    task = await require_task_access(res, user, task_id)
    if task.status in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"task is already {task.status}")
    run = await res.runs.for_task(task.id)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "task has no run")
    await request_cancel(res.redis, run.id)
    if run.status in ("queued", "paused"):  # no worker holds it: finish the cancel here
        await res.tasks.set_status(task.id, "cancelled")
        await res.runs.set_status(run.id, "cancelled")
        await res.events.emit(
            run.id,
            RunLifecycleEvent(type="run.cancelled", run_id=str(run.id)).model_dump(mode="json"),
        )
        return CancelAccepted(task_id=task.id, status="cancelled")
    return CancelAccepted(task_id=task.id, status=task.status)  # type: ignore[arg-type]
