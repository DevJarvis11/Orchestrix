"""Memory API (SPEC 17.1, FR-55): list and search, edit (re-embedded), hard delete with cascade
to derived summaries, and full-user erasure. Deletions are audited without memory text."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from orchestrix_api.deps import CurrentUser, Resources, User, has_role, require_role
from orchestrix_core.db.models import Memory
from orchestrix_core.pii import default_redactor
from orchestrix_memory.clients import EmbeddingUnavailable, GatewayEmbedder
from orchestrix_memory.repo import MemoryRepo, team_ns, user_ns

router = APIRouter(prefix="/v1/memories", tags=["memories"])
admin_router = APIRouter(prefix="/v1/admin/users", tags=["admin"])

Kind = Literal["episode", "lesson", "preference", "fact"]
Admin = Annotated[CurrentUser, require_role("admin")]


class MemoryView(BaseModel):
    id: UUID
    namespace: str
    scope: Literal["user", "team"]
    kind: str
    text: str
    importance: float
    source_run_id: UUID | None
    source_task_id: UUID | None = None
    source_ref: str | None
    access_count: int
    last_used_at: datetime | None
    derived_from: list[UUID]
    created_at: datetime
    can_edit: bool


class MemoryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    importance: float | None = Field(default=None, ge=0, le=1)


class Erased(BaseModel):
    user_id: UUID
    memories_deleted: int


async def _namespaces(res: Resources, user: CurrentUser) -> tuple[list[str], set[str]]:
    """(visible, editable) namespaces: your own; your teams' if you are a reviewer (SPEC 2)."""
    own = user_ns(user.id)
    visible, editable = [own], {own}
    if has_role(user, "reviewer"):
        teams = [team_ns(t) for t in await res.users.team_ids(user.id)]
        visible += teams
        editable |= set(teams)
    return visible, editable


def _view(m: Memory, editable: set[str], admin: bool, task_of: dict[UUID, UUID]) -> MemoryView:
    return MemoryView(
        id=m.id,
        namespace=m.namespace,
        scope="user" if m.namespace.startswith("user:") else "team",
        kind=m.kind,
        text=m.text,
        importance=m.importance,
        source_run_id=m.source_run_id,
        source_task_id=task_of.get(m.source_run_id) if m.source_run_id else None,
        source_ref=m.source_ref,
        access_count=m.access_count,
        last_used_at=m.last_used_at,
        derived_from=list(m.derived_from),
        created_at=m.created_at,
        can_edit=admin or m.namespace in editable,
    )


async def _tasks_for(res: Resources, rows: list[Memory]) -> dict[UUID, UUID]:
    out: dict[UUID, UUID] = {}
    for run_id in {m.source_run_id for m in rows if m.source_run_id}:
        run = await res.runs.get(run_id)
        if run is not None:
            out[run_id] = run.task_id
    return out


@router.get("")
async def list_memories(
    res: Resources,
    user: User,
    kind: Kind | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[MemoryView]:
    visible, editable = await _namespaces(res, user)
    rows = await MemoryRepo(res.sessions).list_for(visible, kind=kind, q=q)
    tasks = await _tasks_for(res, rows)
    return [_view(m, editable, has_role(user, "admin"), tasks) for m in rows]


async def _editable(res: Resources, user: CurrentUser, memory_id: UUID) -> Memory:
    memory = await MemoryRepo(res.sessions).get(memory_id)
    visible, editable = await _namespaces(res, user)
    if memory is None or (memory.namespace not in visible and not has_role(user, "admin")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory not found")
    if memory.namespace not in editable and not has_role(user, "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you may not change this memory")
    return memory


@router.patch("/{memory_id}")
async def edit_memory(memory_id: UUID, body: MemoryPatch, res: Resources, user: User) -> MemoryView:
    memory = await _editable(res, user, memory_id)
    text = default_redactor().text(body.text) if body.text is not None else None
    vector: list[float] | None = None
    if text is not None:
        embedder = GatewayEmbedder.from_settings(res.http, res.settings)
        try:
            vector = (await embedder.embed([text]))[0]
        except EmbeddingUnavailable:
            vector = None  # the backfill job embeds it once the endpoint is back (A5)
    updated = await MemoryRepo(res.sessions).update_text(
        memory_id, text_=text, importance=body.importance, embedding=vector
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory not found")
    await res.audit.write(
        actor_id=user.id,
        action="memory.update",
        target_type="memory",
        target_id=str(memory_id),
        before={"kind": memory.kind, "importance": memory.importance},
        after={"text_changed": text is not None, "importance": updated.importance},
    )
    _, editable = await _namespaces(res, user)
    return _view(updated, editable, has_role(user, "admin"), await _tasks_for(res, [updated]))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: UUID, res: Resources, user: User) -> None:
    memory = await _editable(res, user, memory_id)
    deleted = await MemoryRepo(res.sessions).delete_cascade(memory_id)
    await res.audit.write(  # FR-55: id and kind only, never the memory text
        actor_id=user.id,
        action="memory.delete",
        target_type="memory",
        target_id=str(memory_id),
        before={"kind": memory.kind, "namespace": memory.namespace},
        after={"deleted_ids": [str(d) for d in deleted]},
    )


@admin_router.post("/{user_id}/erase")
async def erase_user(user_id: UUID, res: Resources, user: Admin) -> Erased:
    """FR-55, 13.4: full user data deletion request."""
    count = await MemoryRepo(res.sessions).erase_user(user_id)
    await res.audit.write(
        actor_id=user.id,
        action="user.erase",
        target_type="user",
        target_id=str(user_id),
        after={"memories_deleted": count},
    )
    return Erased(user_id=user_id, memories_deleted=count)
