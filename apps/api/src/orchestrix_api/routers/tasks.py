"""Tasks API (SPEC 17.1): create (FR-02, FR-03, FR-05), read, cancel (FR-04)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from orchestrix_api.deps import TASKS_PER_HOUR, Resources, User, has_role, require_task_access
from orchestrix_api.routers.admin import default_run_budget
from orchestrix_api.security.headers import allow
from orchestrix_core.cancel import request_cancel
from orchestrix_core.db.repos.tasks import AttachmentError
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
    replay_run_ids: list[UUID]  # SPEC 19.4: replays of this task's main run, oldest first
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
    if not has_role(user, "requester"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "requires role: requester")
    if not await allow(res.redis, f"rl:tasks:{user.id}", TASKS_PER_HOUR, 3600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit: 30 tasks/hour")
    team = await res.users.first_team(user.id)
    if team is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you are not a member of any team")
    budget = body.budget or await default_run_budget(res)
    if body.budget is not None and body.budget.max_usd > float(team.monthly_budget_usd):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "budget override exceeds the team cap"
        )
    try:
        created = await res.tasks.create(
            requester_id=user.id,
            team_id=team.id,
            body=body,
            budget=budget,
            idempotency_key=idempotency_key,
            graph_version=res.settings.graph_version,
        )
    except AttachmentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if created.created:
        await enqueue(START_RUN, str(created.run_id))
    return TaskAccepted(task_id=created.task_id, run_id=created.run_id)


class TaskSummary(BaseModel):
    id: UUID
    title: str
    status: TaskStatus
    requester_id: UUID
    team_id: UUID
    run_id: UUID | None
    cost_usd: float
    created_at: datetime
    updated_at: datetime


class TaskPage(BaseModel):
    items: list[TaskSummary]
    next_cursor: str | None  # pass as ?cursor= for the next page


@router.get("")
async def list_tasks(
    res: Resources,
    user: User,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    mine: bool = False,
    team: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> TaskPage:
    """SPEC 17.1: requesters see their own tasks; reviewers their teams'; operators all."""
    team_ids = await res.users.team_ids(user.id)
    scope_all = has_role(user, "operator")
    scope_teams = team_ids if has_role(user, "reviewer") else []
    try:
        before = UUID(cursor) if cursor else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad cursor") from exc
    rows = await res.tasks.page(
        user_id=user.id,
        team_ids=scope_teams,
        all_tasks=scope_all and not mine,
        mine=mine,
        team=team,
        status=status_filter,
        before=before,
        limit=limit + 1,
    )
    items = [
        TaskSummary(
            id=t.id,
            title=t.title,
            status=t.status,  # type: ignore[arg-type]
            requester_id=t.requester_id,
            team_id=t.team_id,
            run_id=run_id,
            cost_usd=float(cost),
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t, run_id, cost in rows[:limit]
    ]
    next_cursor = str(items[-1].id) if len(rows) > limit and items else None
    return TaskPage(items=items, next_cursor=next_cursor)


class DeliverableLink(BaseModel):
    url: str
    expires_in: int


@router.get(
    "/{task_id}/deliverable",
    status_code=status.HTTP_302_FOUND,
    response_model=None,
    responses={200: {"model": DeliverableLink}},
)
async def deliverable(
    task_id: UUID, res: Resources, user: User, format: Literal["redirect", "url"] = "redirect"
) -> RedirectResponse | DeliverableLink:
    """302 to a short-lived presigned URL of the final artifact (SPEC 17.1, 15.4).
    `?format=url` returns the URL as JSON, for browsers that fetch with an Authorization header."""
    task = await require_task_access(res, user, task_id)
    if task.final_artifact_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no deliverable yet")
    artifact = await res.tasks.artifact(task.final_artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no deliverable yet")
    url = await res.objects.presign(
        artifact.object_key, public_endpoint=res.settings.s3_public_endpoint, seconds=300
    )
    if format == "url":
        return DeliverableLink(url=url, expires_in=300)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


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
        replay_run_ids=await res.runs.replay_ids(task.id),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_task(task_id: UUID, res: Resources, user: User) -> CancelAccepted:
    """FR-04: a running run stops at its next dispatch; a queued or paused run stops now."""
    task = await require_task_access(res, user, task_id)
    if task.requester_id != user.id and not has_role(user, "operator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner or an operator may cancel")
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
