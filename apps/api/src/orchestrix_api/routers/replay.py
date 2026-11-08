"""Replay (SPEC 19.4, FR-70, FR-71): checkpoint history, replay from a checkpoint with an
optional state patch, and a diff of two runs. Original runs are never mutated."""

import difflib
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from orchestrix_agents.graph import RunGraph
from orchestrix_api.deps import CurrentUser, Resources, require_role, require_task_access
from orchestrix_core.db.models import LlmCall, Run, ToolCall
from orchestrix_core.queue import REPLAY_RUN, enqueue

router = APIRouter(prefix="/v1/runs", tags=["replay"])
Operator = Annotated[CurrentUser, require_role("operator")]


class CheckpointView(BaseModel):
    checkpoint_id: str
    parent_checkpoint_id: str | None
    step: int | None
    source: str | None  # input | loop | update | fork
    next: list[str]  # the nodes that run after this checkpoint
    created_at: datetime | None
    interrupts: int


class ReplayBody(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=200)
    state_patch: dict[str, Any] | None = None
    as_node: str | None = Field(default=None, max_length=100)


class ReplayAccepted(BaseModel):
    run_id: UUID
    replay_of_run_id: UUID
    checkpoint_id: str


def _graph(res: Resources) -> RunGraph:
    if res.graph is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "checkpoint store unavailable")
    return res.graph


async def _run(res: Resources, user: CurrentUser, run_id: UUID) -> Run:
    run = await res.runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    await require_task_access(res, user, run.task_id)
    return run


def _config(run: Run) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": run.thread_id}
    head = run.head_checkpoint_id or run.replay_from_checkpoint_id
    if head:
        configurable["checkpoint_id"] = head  # this run's own branch only
    return {"configurable": configurable}


@router.get("/{run_id}/checkpoints")
async def checkpoints(run_id: UUID, res: Resources, user: Operator) -> list[CheckpointView]:
    """SPEC 19.4 step 1: the run's branch, newest first."""
    run = await _run(res, user, run_id)
    out: list[CheckpointView] = []
    async for snap in _graph(res).aget_state_history(_config(run)):
        cfg: dict[str, Any] = snap.config.get("configurable", {})
        parent: dict[str, Any] = (snap.parent_config or {}).get("configurable", {})
        meta: dict[str, Any] = dict(snap.metadata or {})
        out.append(
            CheckpointView(
                checkpoint_id=str(cfg.get("checkpoint_id")),
                parent_checkpoint_id=parent.get("checkpoint_id"),
                step=meta.get("step"),
                source=meta.get("source"),
                next=list(snap.next),
                created_at=datetime.fromisoformat(snap.created_at) if snap.created_at else None,
                interrupts=len(snap.interrupts),
            )
        )
    return out


@router.get("/{run_id}/checkpoints/{checkpoint_id}")
async def checkpoint_state(
    run_id: UUID, checkpoint_id: str, res: Resources, user: Operator
) -> dict[str, Any]:
    """The state at one checkpoint, for the replay screen's JSON patch editor."""
    run = await _run(res, user, run_id)
    config: RunnableConfig = {
        "configurable": {"thread_id": run.thread_id, "checkpoint_id": checkpoint_id}
    }
    snap = await _graph(res).aget_state(config)
    if not snap.values:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "checkpoint not found")

    def plain(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if isinstance(value, list):
            return [plain(v) for v in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return value

    return {"values": plain(snap.values), "next": list(snap.next)}


@router.post("/{run_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay(run_id: UUID, body: ReplayBody, res: Resources, user: Operator) -> ReplayAccepted:
    original = await _run(res, user, run_id)
    snap = await _graph(res).aget_state(
        {"configurable": {"thread_id": original.thread_id, "checkpoint_id": body.checkpoint_id}}
    )
    if not snap.values:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "checkpoint not found on this thread")
    created = await res.runs.create_replay(
        original, body.checkpoint_id, body.state_patch, body.as_node, res.settings.graph_version
    )
    await res.audit.write(
        actor_id=user.id,
        action="run.replay",
        target_type="run",
        target_id=str(original.id),
        after={
            "replay_run_id": str(created.id),
            "checkpoint_id": body.checkpoint_id,
            "patched_keys": sorted((body.state_patch or {}).keys()),
        },
    )
    await enqueue(REPLAY_RUN, str(created.id))
    return ReplayAccepted(
        run_id=created.id, replay_of_run_id=original.id, checkpoint_id=body.checkpoint_id
    )


class SubtaskDiff(BaseModel):
    key: str
    status: tuple[str | None, str | None]
    review_scores: tuple[list[float], list[float]]
    output_diff: str  # unified diff of the two outputs


class RunSide(BaseModel):
    run_id: UUID
    kind: str
    status: str
    plan: dict[str, Any] | None
    tool_calls: int
    cost_usd: float
    tokens: int


class RunDiff(BaseModel):
    left: RunSide
    right: RunSide
    plan_diff: str
    subtasks: list[SubtaskDiff]


async def _side(res: Resources, run: Run) -> tuple[RunSide, dict[str, Any]]:
    plan = await res.runs.latest_plan(run.id)
    subtasks = await res.runs.subtasks(run.id, plan.version) if plan else []
    async with res.sessions() as s:
        tools = (
            await s.execute(
                select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run.id)
            )
        ).scalar_one()
        cost, tokens = (
            await s.execute(
                select(
                    func.coalesce(func.sum(LlmCall.cost_usd), 0),
                    func.coalesce(func.sum(LlmCall.input_tokens + LlmCall.output_tokens), 0),
                ).where(LlmCall.run_id == run.id)
            )
        ).one()
    reviews: dict[str, list[float]] = {}
    for review in await res.runs.reviews(run.id):
        reviews.setdefault(review[0] or "(final)", []).append(float(review[1]))
    by_key = {
        s.key: {
            "status": s.status,
            "output": str((s.result or {}).get("output", "")),
            "scores": reviews.get(s.key, []),
        }
        for s in subtasks
    }
    side = RunSide(
        run_id=run.id,
        kind=run.kind,
        status=run.status,
        plan=plan.body if plan else None,
        tool_calls=int(tools),
        cost_usd=round(float(cost), 6),
        tokens=int(tokens),
    )
    return side, by_key


def _udiff(a: str, b: str, left: str, right: str) -> str:
    return "".join(
        difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile=left, tofile=right)
    )


@router.get("/{run_id}/diff/{other_id}")
async def diff(run_id: UUID, other_id: UUID, res: Resources, user: Operator) -> RunDiff:
    """FR-71: plans, subtask outputs (text diff), review scores, tool calls and cost."""
    import json

    left_run, right_run = await _run(res, user, run_id), await _run(res, user, other_id)
    left, lsubs = await _side(res, left_run)
    right, rsubs = await _side(res, right_run)
    keys = sorted(set(lsubs) | set(rsubs))
    return RunDiff(
        left=left,
        right=right,
        plan_diff=_udiff(
            json.dumps(left.plan, indent=2, sort_keys=True),
            json.dumps(right.plan, indent=2, sort_keys=True),
            f"plan:{run_id}",
            f"plan:{other_id}",
        ),
        subtasks=[
            SubtaskDiff(
                key=k,
                status=(lsubs.get(k, {}).get("status"), rsubs.get(k, {}).get("status")),
                review_scores=(
                    lsubs.get(k, {}).get("scores", []),
                    rsubs.get(k, {}).get("scores", []),
                ),
                output_diff=_udiff(
                    lsubs.get(k, {}).get("output", ""),
                    rsubs.get(k, {}).get("output", ""),
                    f"{k}:{run_id}",
                    f"{k}:{other_id}",
                ),
            )
            for k in keys
        ],
    )
