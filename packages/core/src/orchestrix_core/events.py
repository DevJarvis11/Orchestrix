"""Event bus: Postgres outbox, then Redis pub/sub (SPEC 8.3, 17.2). Losing Redis never loses events."""

import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from orchestrix_core.db.repos.events import EventRepo, StoredEvent
from orchestrix_core.logging import get_logger
from orchestrix_core.schemas.events import run_event_adapter

log = get_logger(__name__)


def channel(run_id: UUID | str) -> str:
    return f"run:{run_id}"


def wire_message(event: StoredEvent) -> str:
    """The Redis payload the SSE relay forwards: id for de-duplication, type, data."""
    return json.dumps({"id": event.id, "type": event.type, "data": event.data})


class EventBus:
    def __init__(self, events: EventRepo, redis: Redis) -> None:
        self._events = events
        self._redis = redis

    async def emit(self, run_id: UUID, data: dict[str, Any]) -> int:
        run_event_adapter.validate_python(data)  # only SPEC 17.2 events reach the log
        event_id = await self._events.insert(run_id, data)
        stored = StoredEvent(event_id, run_id, data["type"], data)
        try:
            await self._redis.publish(channel(run_id), wire_message(stored))  # pyright: ignore[reportUnknownMemberType]
        except RedisError as exc:
            log.warning(
                "event publish failed; relay will retry", run_id=str(run_id), error=str(exc)
            )
            return event_id
        await self._events.mark_published([event_id])
        return event_id

    async def relay_unpublished(self) -> int:
        """Scheduler job: publish rows whose live publish failed."""
        pending = await self._events.unpublished()
        done: list[int] = []
        for event in pending:
            try:
                await self._redis.publish(channel(event.run_id), wire_message(event))  # pyright: ignore[reportUnknownMemberType]
            except RedisError:
                break
            done.append(event.id)
        await self._events.mark_published(done)
        return len(done)
