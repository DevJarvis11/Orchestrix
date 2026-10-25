"""Run execution under a lease (SPEC 8.2, 11.4, 14.3).

start_run and resume_run both land here: acquire the lease, stream the graph from its last
checkpoint, publish events, mirror pending interrupts into escalations, then release the lease.
"""

import asyncio
import os
import socket
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_agents.context import RunContext, Services
from orchestrix_agents.graph import RunGraph, build_graph
from orchestrix_agents.llm import GatewayChatProvider, resolve_models
from orchestrix_agents.serde import make_serializer
from orchestrix_agents.specialists.stub import StubSpecialistRunner
from orchestrix_core.cancel import RedisCancelCheck
from orchestrix_core.config import Settings, get_settings
from orchestrix_core.db.models import Run, Task
from orchestrix_core.db.repos.escalations import EscalationRepo
from orchestrix_core.db.repos.events import EventRepo
from orchestrix_core.db.repos.runs import RunRepo
from orchestrix_core.db.repos.tasks import TaskRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker
from orchestrix_core.events import EventBus
from orchestrix_core.logging import get_logger, log_context
from orchestrix_core.schemas import Budget, TaskInput
from orchestrix_core.schemas.events import EscalationEvent, RunLifecycleEvent
from orchestrix_core.state_machine import TERMINAL
from orchestrix_core.storage import ObjectStore, S3ObjectStore
from orchestrix_worker.fake_models import FakeChatProvider, FakeToolSpecialist
from orchestrix_worker.services import SqlRunRecorder

log = get_logger(__name__)

WORKER_ID: Final = f"{socket.gethostname()}:{os.getpid()}"
MAX_RECLAIMS: Final = 3  # SPEC 8.2 step 5: a run reclaimed 3 times fails with crash_loop


@dataclass
class WorkerDeps:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    pool: AsyncConnectionPool[Any]
    graph: RunGraph
    runs: RunRepo
    tasks: TaskRepo
    events: EventBus
    escalations: EscalationRepo
    recorder: SqlRunRecorder


_deps: WorkerDeps | None = None
_deps_lock = asyncio.Lock()


async def get_deps() -> WorkerDeps:
    """One set of connections per worker process, created on first use."""
    global _deps
    async with _deps_lock:
        if _deps is None:
            _deps = await build_deps(get_settings())
        return _deps


async def build_deps(settings: Settings, store: ObjectStore | None = None) -> WorkerDeps:
    sessions = create_sessionmaker(create_engine(settings))
    redis = Redis.from_url(settings.redis_url)  # pyright: ignore[reportUnknownMemberType]
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        settings.checkpointer_dsn,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        max_size=10,
        open=False,
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool, serde=make_serializer())  # pyright: ignore[reportArgumentType]
    tasks = TaskRepo(sessions)
    return WorkerDeps(
        settings=settings,
        sessions=sessions,
        redis=redis,
        pool=pool,
        graph=build_graph(checkpointer),
        runs=RunRepo(sessions),
        tasks=tasks,
        events=EventBus(EventRepo(sessions), redis),
        escalations=EscalationRepo(sessions),
        recorder=SqlRunRecorder(sessions, tasks, store or S3ObjectStore(settings)),
    )


def graph_config(run: Run, budget: Budget) -> RunnableConfig:
    return {"configurable": {"thread_id": run.thread_id}, "recursion_limit": budget.max_steps}


async def build_context(deps: WorkerDeps, run: Run, task: Task) -> RunContext:
    settings = deps.settings
    defaults = {str(role): model for role, model in settings.default_models.items()}
    resolved = resolve_models(task.model_overrides, {}, defaults)
    models = await deps.runs.set_model_config(run.id, {str(r): m for r, m in resolved.items()})
    if settings.fake_models:
        llm: Any = FakeChatProvider()
        specialists: Any = FakeToolSpecialist(deps.sessions, settings.fake_model_delay_seconds)
    else:
        llm = GatewayChatProvider(
            base_url=settings.litellm_url,
            api_key=settings.litellm_master_key,
            models=models,  # pyright: ignore[reportArgumentType]
            prices=settings.model_prices,
        )
        specialists = StubSpecialistRunner()
    services = Services(
        llm=llm,
        specialists=specialists,
        recorder=deps.recorder,
        cancel=RedisCancelCheck(deps.redis),
    )
    return RunContext(
        run_id=str(run.id),
        task_id=str(task.id),
        user_id=str(task.requester_id),
        team_id=str(task.team_id),
        budget=Budget.model_validate(task.budget),
        services=services,
        max_concurrency=settings.max_concurrency,
        plan_confidence_threshold=settings.plan_confidence_threshold,
        review_score_threshold=settings.review_score_threshold,
    )


async def _heartbeat(deps: WorkerDeps, run_id: UUID, lost: asyncio.Event) -> None:
    s = deps.settings
    while True:
        await asyncio.sleep(s.heartbeat_seconds)
        if not await deps.runs.heartbeat(run_id, WORKER_ID, s.lease_seconds):
            log.warning("run lease lost; stopping after the current step", run_id=str(run_id))
            lost.set()
            return


async def execute_run(deps: WorkerDeps, run_id: UUID) -> None:
    """Run (or continue) one run while holding its lease."""
    if not await deps.runs.acquire_lease(run_id, WORKER_ID, deps.settings.lease_seconds):
        log.info("run is leased by another worker; skipping", run_id=str(run_id))
        return
    lost = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(deps, run_id, lost))
    try:
        with log_context(run_id=str(run_id)):
            await _execute(deps, run_id, lost)
    finally:
        heartbeat.cancel()


async def _execute(deps: WorkerDeps, run_id: UUID, lost: asyncio.Event) -> None:
    run = await deps.runs.get(run_id)
    if run is None:
        raise LookupError(run_id)
    task = await deps.tasks.get(run.task_id)
    if task is None:
        raise LookupError(run.task_id)

    if run.reclaim_count >= MAX_RECLAIMS:
        await _fail(deps, run, task, "crash_loop")
        return

    ctx = await build_context(deps, run, task)
    budget = Budget.model_validate(task.budget)
    config = graph_config(run, budget)
    snapshot = await deps.graph.aget_state(config)

    value: Any
    if not snapshot.values:
        value = {
            "task": TaskInput(
                id=str(task.id),
                title=task.title,
                description=task.description,
                require_plan_approval=task.require_plan_approval,
            ),
            "budget": budget,
        }
    else:
        decisions = await deps.escalations.resolved_decisions(run_id)
        pending = {i.id for i in snapshot.interrupts}
        to_apply = {k: v for k, v in decisions.items() if k in pending}
        if pending and not to_apply:
            log.info("run is waiting for a human decision; nothing to do")
            await deps.runs.release(run_id, WORKER_ID, "paused")
            return
        value = Command(resume=to_apply) if to_apply else None  # None: continue after a crash
        reason = "decision" if to_apply else "reclaimed" if run.reclaim_count else "restart"
        await deps.events.emit(
            run_id,
            RunLifecycleEvent(type="run.resumed", run_id=str(run_id), reason=reason).model_dump(
                mode="json"
            ),
        )

    try:
        async for mode, chunk in deps.graph.astream(
            value, config, context=ctx, stream_mode=["custom", "updates"], durability="sync"
        ):
            if lost.is_set():
                return  # another worker may own the run now: write nothing more
            if mode == "custom":
                await deps.events.emit(run_id, cast(dict[str, Any], chunk))
    except Exception as exc:
        if lost.is_set():
            return
        log.exception("run failed")
        await _fail(deps, run, task, f"error: {type(exc).__name__}: {exc}"[:500])
        return

    snapshot = await deps.graph.aget_state(config)
    checkpoint_id = snapshot.config.get("configurable", {}).get("checkpoint_id")
    if checkpoint_id:
        await deps.runs.set_head_checkpoint(run_id, str(checkpoint_id))

    pending_ids = {i.id for i in snapshot.interrupts}
    await deps.escalations.resolve_missing(run_id, pending_ids)
    if snapshot.interrupts:
        await _pause(deps, run_id, task, list(snapshot.interrupts))
        return

    refreshed = await deps.tasks.get(task.id)
    status = refreshed.status if refreshed else "failed"
    run_status = {"completed": "completed", "cancelled": "cancelled"}.get(
        status, "failed" if status in TERMINAL else "running"
    )
    await deps.runs.release(run_id, WORKER_ID, run_status)


async def _pause(deps: WorkerDeps, run_id: UUID, task: Task, interrupts: list[Any]) -> None:
    """SPEC 14.3 step 2: escalation rows, task status, run paused, lease released."""
    for intr in interrupts:
        payload: dict[str, Any] = dict(intr.value)
        if await deps.escalations.upsert_open(run_id, intr.id, payload):
            await deps.events.emit(
                run_id,
                EscalationEvent(
                    type="escalation.created",
                    run_id=str(run_id),
                    interrupt_id=intr.id,
                    trigger=payload["trigger"],
                    level=payload["level"],
                    subtask=payload.get("subtask"),
                ).model_dump(mode="json"),
            )
    plan_gate = any(dict(i.value).get("level") == "approve_plan" for i in interrupts)
    await deps.tasks.set_status(
        task.id, "awaiting_plan_approval" if plan_gate else "awaiting_human"
    )
    await deps.runs.release(run_id, WORKER_ID, "paused")


async def _fail(deps: WorkerDeps, run: Run, task: Task, reason: str) -> None:
    current = await deps.tasks.get(task.id)
    if current is not None and current.status not in TERMINAL:
        await deps.tasks.set_status(task.id, "failed", reason)
    await deps.events.emit(
        run.id,
        RunLifecycleEvent(type="run.failed", run_id=str(run.id), reason=reason).model_dump(
            mode="json"
        ),
    )
    await deps.runs.release(run.id, WORKER_ID, "failed")


async def close_deps(deps: WorkerDeps) -> None:
    await deps.pool.close()
    await deps.redis.aclose()
