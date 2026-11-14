"""Dev-mode password login (AUTH_MODE=dev only): exchanges username/password for a dev JWT."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from orchestrix_api.deps import Resources
from orchestrix_api.security import dev_jwt
from orchestrix_api.security.headers import allow
from orchestrix_core.db.repos.dev_logins import DevLoginRepo

router = APIRouter(prefix="/v1/auth", tags=["auth"])

LOGINS_PER_MINUTE = 10


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str


@router.post("/login")
async def login(body: LoginRequest, request: Request, res: Resources) -> LoginResponse:
    if res.settings.auth_mode != "dev":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "password login is disabled")
    client = request.client.host if request.client else "unknown"
    if not await allow(res.redis, f"rl:login:{client}", LOGINS_PER_MINUTE, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")
    user = await DevLoginRepo(res.sessions).authenticate(
        body.username.strip().lower(), body.password
    )
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    token = dev_jwt.issue(
        res.settings.dev_jwt_secret.get_secret_value(),
        sub=user.sub,
        email=user.email,
        name=user.name,
        roles=user.roles,
    )
    return LoginResponse(access_token=token)
