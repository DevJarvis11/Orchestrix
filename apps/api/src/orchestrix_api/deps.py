"""FastAPI dependencies: app resources and the authenticated user (SPEC 9)."""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_agents.graph import RunGraph
from orchestrix_api.security import dev_jwt, oidc
from orchestrix_api.security.headers import allow
from orchestrix_core.config import Settings
from orchestrix_core.db.models import Task
from orchestrix_core.db.repos.audit import AuditRepo
from orchestrix_core.db.repos.escalations import EscalationRepo
from orchestrix_core.db.repos.events import EventRepo
from orchestrix_core.db.repos.runs import RunRepo
from orchestrix_core.db.repos.tasks import TaskRepo, UserRepo
from orchestrix_core.events import EventBus
from orchestrix_core.storage import S3ObjectStore
from orchestrix_policy.client import PolicyClient


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
    objects: S3ObjectStore
    escalations: EscalationRepo
    audit: AuditRepo
    policy: PolicyClient
    graph: "RunGraph | None" = None  # read-only view of checkpoints (replay history, SPEC 19.4)


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
    settings = res.settings
    try:
        if settings.auth_mode == "dev":
            claims = dev_jwt.verify(
                settings.dev_jwt_secret.get_secret_value(), credentials.credentials
            )
        else:
            claims = await oidc.verify(
                credentials.credentials,
                issuer=settings.oidc_issuer,
                audience=settings.oidc_audience,
                jwks=settings.oidc_jwks_url,
            )
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if not await allow(res.redis, f"rl:api:{claims['sub']}", API_PER_MINUTE, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit: 600 requests/minute")
    email = str(claims.get("email", ""))
    name = str(claims.get("name") or claims.get("preferred_username") or email)
    user = await res.users.upsert(str(claims["sub"]), email, name)
    groups = claims.get("groups")
    if isinstance(groups, list):  # Keycloak groups are the teams (Keycloak is the source of truth)
        await res.users.sync_teams(user.id, [str(g).lstrip("/") for g in groups])  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    roles = frozenset(str(r) for r in claims.get("realm_access", {}).get("roles", []))
    return CurrentUser(id=user.id, sub=str(claims["sub"]), email=email, roles=roles)


User = Annotated[CurrentUser, Depends(current_user)]

ROLES = ("requester", "reviewer", "operator", "admin")
API_PER_MINUTE = 600  # SPEC 15.4
TASKS_PER_HOUR = 30


def has_role(user: CurrentUser, *roles: str) -> bool:
    """Admins can do everything (SPEC 2), except where a rule says otherwise."""
    return "admin" in user.roles or any(r in user.roles for r in roles)


def require_role(*roles: str) -> Any:
    """FastAPI dependency: 403 unless the user holds one of `roles` (or admin)."""

    async def check(user: User) -> CurrentUser:
        if not has_role(user, *roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {' or '.join(roles)}")
        return user

    return Depends(check)


async def require_task_access(res: AppResources, user: CurrentUser, task_id: UUID) -> Task:
    """SPEC 2/17.1: the owner, a reviewer of the task's team, or an operator. 404 hides others'."""
    task = await res.tasks.get(task_id)
    allowed = task is not None and (
        task.requester_id == user.id
        or has_role(user, "operator")
        or (has_role(user, "reviewer") and await res.users.is_team_member(user.id, task.team_id))
    )
    if task is None or not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return task
