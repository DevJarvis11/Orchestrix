"""Job queue: Taskiq on Redis Streams (SPEC 7.1). Leases in Postgres make delivery safe (SPEC 8.2).

The broker lives in core so the API can enqueue by task name without importing worker code.
"""

from functools import lru_cache
from typing import Final

from taskiq.kicker import AsyncKicker
from taskiq_redis import RedisStreamBroker

from orchestrix_core.config import get_settings

START_RUN: Final = "orchestrix.start_run"
RESUME_RUN: Final = "orchestrix.resume_run"


@lru_cache
def get_broker() -> RedisStreamBroker:
    return RedisStreamBroker(url=get_settings().redis_url, queue_name="orchestrix")


async def enqueue(task_name: str, run_id: str) -> None:
    await AsyncKicker(task_name=task_name, broker=get_broker(), labels={}).kiq(run_id)
