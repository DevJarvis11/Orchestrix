"""Run cancellation flag (FR-04). The API sets it; dispatch checks it before scheduling work."""

from uuid import UUID

from redis.asyncio import Redis

CANCEL_TTL_SECONDS = 7 * 24 * 3600


def cancel_key(run_id: UUID | str) -> str:
    return f"run:{run_id}:cancel"


async def request_cancel(redis: Redis, run_id: UUID | str) -> None:
    await redis.set(cancel_key(run_id), "1", ex=CANCEL_TTL_SECONDS)  # pyright: ignore[reportUnknownMemberType]


class RedisCancelCheck:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def is_cancelled(self, run_id: str) -> bool:
        return bool(await self._redis.exists(cancel_key(run_id)))  # pyright: ignore[reportUnknownMemberType]
