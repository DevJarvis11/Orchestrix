"""Container healthcheck for worker and scheduler: the broker's Redis must answer PING."""

import asyncio
import sys

from redis.asyncio import Redis
from redis.exceptions import RedisError

from orchestrix_core.config import get_settings


async def check(url: str) -> bool:
    redis = Redis.from_url(url, socket_connect_timeout=2)  # pyright: ignore[reportUnknownMemberType]
    try:
        return bool(await redis.ping())  # pyright: ignore[reportUnknownMemberType]
    except RedisError:
        return False
    finally:
        await redis.aclose()


def main() -> None:
    sys.exit(0 if asyncio.run(check(get_settings().redis_url)) else 1)


if __name__ == "__main__":
    main()
