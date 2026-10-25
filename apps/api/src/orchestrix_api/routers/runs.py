"""Run event stream (SPEC 17.2): replay from Postgres, then relay Redis, de-duplicated by id."""

import json
from collections.abc import AsyncIterable
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from orchestrix_api.deps import Resources, User, require_task_access
from orchestrix_core.events import channel

router = APIRouter(prefix="/v1/runs", tags=["runs"])

FINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})


def _sse(event_id: int, event_type: str, data: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(id=str(event_id), event=event_type, data=data)


@router.get("/{run_id}/events", response_class=EventSourceResponse)
async def run_events(
    run_id: UUID,
    res: Resources,
    user: User,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> AsyncIterable[ServerSentEvent]:
    run = await res.runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    await require_task_access(res, user, run.task_id)
    try:
        last = int(last_event_id) if last_event_id else 0
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Last-Event-ID must be an event id"
        ) from exc

    # Subscribe before replaying so nothing published in between is missed; ids de-duplicate.
    pubsub = res.redis.pubsub()  # pyright: ignore[reportUnknownMemberType]
    await pubsub.subscribe(channel(run_id))  # pyright: ignore[reportUnknownMemberType]
    try:
        for event in await res.event_log.after(run_id, last):
            yield _sse(event.id, event.type, event.data)
            last = event.id
            if event.type in FINAL_EVENTS:
                return
        while True:
            message = cast(
                dict[str, Any] | None,
                await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),  # pyright: ignore[reportUnknownMemberType]
            )
            if message is None:
                continue
            payload: dict[str, Any] = json.loads(message["data"])
            if payload["id"] <= last:
                continue  # already replayed from Postgres
            yield _sse(payload["id"], payload["type"], payload["data"])
            last = payload["id"]
            if payload["type"] in FINAL_EVENTS:
                return
    finally:
        await pubsub.unsubscribe()  # pyright: ignore[reportUnknownMemberType]
        await pubsub.aclose()  # pyright: ignore[reportUnknownMemberType]
