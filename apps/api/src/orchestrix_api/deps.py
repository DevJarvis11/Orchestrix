"""FastAPI dependencies: app resources and the authenticated user (SPEC 9)."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_api.security import dev_jwt
from orchestrix_core.config import Settings
from orchestrix_core.db.models import Task
from orchestrix_core.db.repos.events import EventRepo
from orchestrix_core.db.repos.runs import RunRepo
from orchestrix_core.db.repos.tasks import TaskRepo, UserRepo
from orchestrix_core.events import EventBus


@dataclass
class AppResources:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    http: httpx.AsyncClient
    tasks: TaskRepo
    runs: RunRepo
    users: UserRepo
    event_log: EventRepo
    events: EventBus


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    sub: str
    email: str
    roles: frozenset[str]


def resources(request: Request) -> AppResources:
    return request.app.state.resources


Resources = Annotated[AppResources, Depends(resources)]

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    res: Resources,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "missing or invalid bearer token",
        {"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    if res.settings.auth_mode != "dev":
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "OIDC validation arrives in M7")
    try:
        claims = dev_jwt.verify(
            res.settings.dev_jwt_secret.get_secret_value(), credentials.credentials
        )
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    email = str(claims.get("email", ""))
    user = await res.users.upsert(str(claims["sub"]), email, str(claims.get("name") or email))
    roles = frozenset(str(r) for r in claims.get("realm_access", {}).get("roles", []))
    return CurrentUser(id=user.id, sub=str(claims["sub"]), email=email, roles=roles)


User = Annotated[CurrentUser, Depends(current_user)]


async def require_task_access(res: AppResources, user: CurrentUser, task_id: UUID) -> Task:
    """Owner or a member of the task's team (full RBAC arrives in M7). 404 hides others' tasks."""
    task = await res.tasks.get(task_id)
    if task is None or (
        task.requester_id != user.id and not await res.users.is_team_member(user.id, task.team_id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return task
