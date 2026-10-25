"""Dev-mode tokens (SPEC 21.1 AUTH_MODE=dev): locally signed HS256 JWTs shaped like Keycloak's.

`make dev-token` provisions a demo user and team, then prints a token for curl.
Refused unless ORCHESTRIX_ENV=development (enforced in Settings). Keycloak replaces this in M7.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from orchestrix_core.config import get_settings
from orchestrix_core.db.repos.tasks import UserRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker

ALGORITHM = "HS256"
ISSUER = "orchestrix-dev"
AUDIENCE = "orchestrix-api"
ROLES = ("requester", "reviewer", "operator", "admin")


def issue(
    secret: str, *, sub: str, email: str, name: str, roles: list[str], hours: int = 12
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "email": email,
        "name": name,
        "realm_access": {"roles": roles},  # Keycloak's realm-role claim shape
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)  # pyright: ignore[reportUnknownMemberType]


def verify(secret: str, token: str) -> dict[str, Any]:
    return jwt.decode(  # pyright: ignore[reportUnknownMemberType]
        token, secret, algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER
    )


async def _provision(email: str, team: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    users = UserRepo(create_sessionmaker(engine))
    sub = f"dev|{email}"
    user = await users.upsert(sub, email, email.split("@")[0])
    await users.ensure_team_member(user.id, team)
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a dev JWT for a provisioned demo user.")
    parser.add_argument("--email", default="demo@orchestrix.local")
    parser.add_argument("--team", default="demo")
    parser.add_argument("--roles", default=",".join(ROLES))
    args = parser.parse_args()
    settings = get_settings()
    asyncio.run(_provision(args.email, args.team))
    print(
        issue(
            settings.dev_jwt_secret.get_secret_value(),
            sub=f"dev|{args.email}",
            email=args.email,
            name=args.email.split("@")[0],
            roles=args.roles.split(","),
        )
    )


if __name__ == "__main__":
    main()
