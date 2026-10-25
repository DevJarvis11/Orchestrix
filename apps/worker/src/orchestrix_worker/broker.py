"""Taskiq broker on Redis Streams (SPEC 7.1). Acknowledged delivery; leases live in Postgres."""

from taskiq_redis import RedisStreamBroker

from orchestrix_core.config import get_settings

broker = RedisStreamBroker(url=get_settings().redis_url, queue_name="orchestrix")
