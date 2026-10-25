"""Tasks: creation with Idempotency-Key (FR-05) and validated status changes (FR-01)."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import Run, Task, Team, TeamMember, User
from orchestrix_core.schemas import Budget, TaskCreate, TaskStatus
from orchestrix_core.state_machine import check_transition

IDEMPOTENCY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Created:
    task_id: UUID
    run_id: UUID
    created: bool  # False = an earlier request with the same Idempotency-Key


class TaskRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        requester_id: UUID,
        team_id: UUID,
        body: TaskCreate,
        budget: Budget,
        idempotency_key: str | None,
        graph_version: str,
    ) -> Created:
        async with self._sessions.begin() as s:
            if idempotency_key:
                existing = await s.execute(
                    select(Task.id, Run.id)
                    .join(Run, (Run.task_id == Task.id) & (Run.kind == "main"))
                    .where(
                        Task.requester_id == requester_id,
                        Task.idempotency_key == idempotency_key,
                        Task.created_at > func.now() - IDEMPOTENCY_WINDOW,
                    )
                )
                if row := existing.first():
                    return Created(task_id=row[0], run_id=row[1], created=False)
            task = Task(
                team_id=team_id,
                requester_id=requester_id,
                title=body.title,
                description=body.description,
                status="queued",
                require_plan_approval=body.require_plan_approval,
                budget=budget.model_dump(),
                model_overrides=dict(body.model_overrides),
                idempotency_key=idempotency_key,
            )
            s.add(task)
            await s.flush()
            run = Run(
                task_id=task.id,
                kind="main",
                status="queued",
                thread_id="",
                graph_version=graph_version,
            )
            s.add(run)
            await s.flush()
            run.thread_id = str(run.id)  # main run: thread id = run id (SPEC 10)
            return Created(task_id=task.id, run_id=run.id, created=True)

    async def get(self, task_id: UUID) -> Task | None:
        async with self._sessions() as s:
            return await s.get(Task, task_id)

    async def set_status(
        self, task_id: UUID, status: TaskStatus, reason: str | None = None
    ) -> bool:
        """Apply an FR-01 transition. Repeating the current status is a harmless no-op."""
        async with self._sessions.begin() as s:
            task = await s.get(Task, task_id, with_for_update=True)
            if task is None:
                raise LookupError(task_id)
            if not check_transition(task.status, status):  # type: ignore[arg-type]
                return False
            task.status = status
            if reason is not None:
                task.failure_reason = reason
            return True

    async def clear_expired_idempotency_keys(self) -> int:
        """Plan improvement 12: keys only block reuse inside the FR-05 24 h window."""
        async with self._sessions.begin() as s:
            result = await s.execute(
                text(
                    "UPDATE tasks SET idempotency_key = NULL "
                    "WHERE idempotency_key IS NOT NULL AND created_at < now() - interval '24 hours' "
                    "RETURNING id"
                )
            )
            return len(result.all())


class UserRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert(self, keycloak_sub: str, email: str, display_name: str) -> User:
        """Just-in-time provisioning from token claims (Keycloak is the source of truth)."""
        async with self._sessions.begin() as s:
            user = (
                await s.execute(select(User).where(User.keycloak_sub == keycloak_sub))
            ).scalar_one_or_none()
            if user is None:
                user = User(keycloak_sub=keycloak_sub, email=email, display_name=display_name)
                s.add(user)
                await s.flush()
            return user

    async def first_team(self, user_id: UUID) -> Team | None:
        async with self._sessions() as s:
            result = await s.execute(
                select(Team)
                .join(TeamMember, TeamMember.team_id == Team.id)
                .where(TeamMember.user_id == user_id)
                .order_by(Team.name)
            )
            return result.scalars().first()

    async def ensure_team_member(self, user_id: UUID, team_name: str) -> Team:
        async with self._sessions.begin() as s:
            team = (
                await s.execute(select(Team).where(Team.name == team_name))
            ).scalar_one_or_none()
            if team is None:
                team = Team(name=team_name)
                s.add(team)
                await s.flush()
            member = await s.get(TeamMember, (team.id, user_id))
            if member is None:
                s.add(TeamMember(team_id=team.id, user_id=user_id))
            return team

    async def is_team_member(self, user_id: UUID, team_id: UUID) -> bool:
        async with self._sessions() as s:
            return await s.get(TeamMember, (team_id, user_id)) is not None
