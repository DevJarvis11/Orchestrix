"""Dev-mode username/password logins: scrypt-hashed passwords in Postgres."""

import hashlib
import hmac
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_core.db.models import DevLogin, User

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P)
    except ValueError:
        return False
    return hmac.compare_digest(actual, bytes.fromhex(digest))


@dataclass(frozen=True)
class LoginUser:
    user_id: UUID
    sub: str
    email: str
    name: str
    roles: list[str]


class DevLoginRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def set_password(
        self, username: str, user_id: UUID, password: str, roles: list[str]
    ) -> None:
        """Upsert keyed by username, so re-seeding resets the password and roles."""
        stmt = pg_insert(DevLogin).values(
            username=username, user_id=user_id, password_hash=hash_password(password), roles=roles
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DevLogin.username],
            set_={
                "user_id": stmt.excluded.user_id,
                "password_hash": stmt.excluded.password_hash,
                "roles": stmt.excluded.roles,
            },
        )
        async with self._sessions.begin() as s:
            await s.execute(stmt)

    async def authenticate(self, username: str, password: str) -> LoginUser | None:
        async with self._sessions() as s:
            row = (
                await s.execute(
                    select(DevLogin, User)
                    .join(User, User.id == DevLogin.user_id)
                    .where(DevLogin.username == username)
                )
            ).one_or_none()
        if row is None:
            hash_password(password)  # keep timing similar for unknown usernames
            return None
        login, user = row
        if not verify_password(password, login.password_hash):
            return None
        return LoginUser(user.id, user.keycloak_sub, user.email, user.display_name, login.roles)
