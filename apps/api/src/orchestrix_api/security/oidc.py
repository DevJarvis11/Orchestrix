"""OIDC access-token validation (SPEC 15.4, NFR-05): PyJWKClient against Keycloak's cached JWKS,
checking signature, iss, aud and exp."""

import asyncio
from functools import lru_cache
from typing import Any

import jwt

ALGORITHMS = ["RS256", "ES256", "PS256"]


@lru_cache(maxsize=4)
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)


def jwks_url(issuer: str, override: str | None) -> str:
    return override or f"{issuer.rstrip('/')}/protocol/openid-connect/certs"


def _verify(token: str, url: str, issuer: str, audience: str) -> dict[str, Any]:
    key = _jwks(url).get_signing_key_from_jwt(token)
    return jwt.decode(  # pyright: ignore[reportUnknownMemberType]
        token,
        key.key,
        algorithms=ALGORITHMS,
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )


async def verify(token: str, *, issuer: str, audience: str, jwks: str | None) -> dict[str, Any]:
    """Raises jwt.PyJWTError on any failure. The JWKS fetch is blocking, so it runs in a thread."""
    return await asyncio.to_thread(_verify, token, jwks_url(issuer, jwks), issuer, audience)
