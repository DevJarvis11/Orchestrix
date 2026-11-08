"""Tasks: creation with Idempotency-Key (FR-05) and validated status changes (FR-01)."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import Artifact, LlmCall, Run, Task, Team, TeamMember, User
from orchestrix_core.schemas import Budget, TaskCreate, TaskStatus
from orchestrix_core.state_machine import check_transition

IDEMPOTENCY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Created:
    task_id: UUID
    run_id: UUID
    created: bool  # False = an earlier request with the same Idempotency-Key


class AttachmentError(ValueError):
    pass


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
                require_final_approval=body.require_final_approval,
                budget=budget.model_dump(),
                model_overrides=dict(body.model_overrides),
                idempotency_key=idempotency_key,
            )
            s.add(task)
            await s.flush()
            if body.attachments:
                rows = await s.execute(
                    update(Artifact)
                    .where(
                        Artifact.id.in_(body.attachments),
                        Artifact.uploaded_by == requester_id,
                        Artifact.task_id.is_(None),
                    )
                    .values(task_id=task.id)
                    .returning(Artifact.id, Artifact.name)
                )
                attached = rows.all()
                if len(attached) != len(set(body.attachments)):
                    raise AttachmentError("attachments must be your own uploads, not yet attached")
                if len({name for _, name in attached}) != len(attached):
                    raise AttachmentError("attachment file names must be unique within a task")
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

    async def page(
        self,
        *,
        user_id: UUID,
        team_ids: list[UUID],
        all_tasks: bool,
        mine: bool,
        team: UUID | None,
        status: str | None,
        before: UUID | None,
        limit: int,
    ) -> list[tuple[Task, UUID | None, float]]:
        """Newest first; uuidv7 ids sort by creation time, so the id is the cursor."""
        cost = (
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0))
            .join(Run, Run.id == LlmCall.run_id)
            .where(Run.task_id == Task.id)
            .scalar_subquery()
        )
        main_run = (
            select(Run.id)
            .where(Run.task_id == Task.id, Run.kind == "main")
            .order_by(Run.created_at)
            .limit(1)
            .scalar_subquery()
        )
        q = select(Task, main_run, cost).order_by(Task.id.desc()).limit(limit)
        if mine or not all_tasks:
            visible = Task.requester_id == user_id
            if not mine and team_ids:
                visible = or_(visible, Task.team_id.in_(team_ids))
            q = q.where(visible)
        if team is not None:
            q = q.where(Task.team_id == team)
        if status:
            q = q.where(Task.status == status)
        if before is not None:
            q = q.where(Task.id < before)
        async with self._sessions() as s:
            return [(t, r, float(c)) for t, r, c in (await s.execute(q)).all()]

    async def artifact(self, artifact_id: UUID) -> Artifact | None:
        async with self._sessions() as s:
            return await s.get(Artifact, artifact_id)

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

    async def attachments(self, task_id: UUID) -> dict[str, str]:
        """name -> artifact id for a task's attachments."""
        async with self._sessions() as s:
            rows = await s.execute(
                select(Artifact.name, Artifact.id).where(
                    Artifact.task_id == task_id, Artifact.run_id.is_(None)
                )
            )
            return {name: str(artifact_id) for name, artifact_id in rows.all()}


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

    async def sync_teams(self, user_id: UUID, team_names: list[str]) -> None:
        """Mirror the token's groups into team_members (Keycloak is the source of truth).

        Reads first and writes only when membership changed; inserts use ON CONFLICT DO NOTHING,
        so concurrent first requests of a new user cannot collide."""
        wanted = set(team_names)
        async with self._sessions() as s:
            current = set(
                (
                    await s.execute(
                        select(Team.name)
                        .join(TeamMember, TeamMember.team_id == Team.id)
                        .where(TeamMember.user_id == user_id)
                    )
                ).scalars()
            )
        if current == wanted:
            return
        async with self._sessions.begin() as s:
            if wanted:
                await s.execute(
                    pg_insert(Team)
                    .values([{"name": n} for n in sorted(wanted)])
                    .on_conflict_do_nothing(index_elements=["name"])
                )
                ids = list(
                    (await s.execute(select(Team.id).where(Team.name.in_(wanted)))).scalars()
                )
                await s.execute(
                    pg_insert(TeamMember)
                    .values([{"team_id": t, "user_id": user_id} for t in ids])
                    .on_conflict_do_nothing()
                )
            stale = select(Team.id).where(Team.name.in_(current - wanted))
            await s.execute(
                delete(TeamMember).where(
                    TeamMember.user_id == user_id, TeamMember.team_id.in_(stale)
                )
            )

    async def team_ids(self, user_id: UUID) -> list[UUID]:
        async with self._sessions() as s:
            rows = await s.execute(select(TeamMember.team_id).where(TeamMember.user_id == user_id))
            return list(rows.scalars())

    async def is_team_member(self, user_id: UUID, team_id: UUID) -> bool:
        async with self._sessions() as s:
            return await s.get(TeamMember, (team_id, user_id)) is not None
