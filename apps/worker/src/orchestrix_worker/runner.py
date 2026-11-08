"""Run execution under a lease (SPEC 8.2, 11.4, 14.3).

start_run and resume_run both land here: acquire the lease, stream the graph from its last
checkpoint, publish events, mirror pending interrupts into escalations, then release the lease.
"""

import asyncio
import os
import socket
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, cast
from uuid import UUID

import httpx
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
from orchestrix_agents.specialists.agent import AgentSpecialistRunner
from orchestrix_core.cancel import RedisCancelCheck
from orchestrix_core.config import Settings, get_settings
from orchestrix_core.db.models import Run, Task
from orchestrix_core.db.repos.audit import AuditRepo
from orchestrix_core.db.repos.escalations import EscalationRepo
from orchestrix_core.db.repos.events import EventRepo
from orchestrix_core.db.repos.runs import RunRepo
from orchestrix_core.db.repos.tasks import TaskRepo
from orchestrix_core.db.session import create_engine, create_sessionmaker
from orchestrix_core.events import EventBus
from orchestrix_core.logging import get_logger, log_context
from orchestrix_core.notify import Notifier
from orchestrix_core.pii import default_redactor
from orchestrix_core.schemas import Budget, TaskInput
from orchestrix_core.schemas.events import BudgetEvent, EscalationEvent, RunLifecycleEvent
from orchestrix_core.state_machine import TERMINAL
from orchestrix_core.storage import ObjectStore, S3ObjectStore
from orchestrix_core.telemetry import flush as flush_traces
from orchestrix_core.telemetry import langchain_handler, run_trace, span
from orchestrix_memory.clients import GatewayEmbedder, GatewayReranker, LocalReranker
from orchestrix_memory.repo import MemoryRepo
from orchestrix_memory.service import MemoryService
from orchestrix_policy.client import PolicyClient
from orchestrix_policy.escalation_policy import EscalationPolicy, load_escalation_policy
from orchestrix_tools.client import ToolBox
from orchestrix_worker.fake_models import FakeChatProvider, FakeToolSpecialist
from orchestrix_worker.observability import NodeTimer, on_event, run_finished
from orchestrix_worker.services import (
    SqlArtifactBridge,
    SqlLlmCallSink,
    SqlPolicyGate,
    SqlRunRecorder,
    SqlToolCallStore,
    admin_model_defaults,
    enabled_tools,
    sync_tool_registry,
)

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
    tool_calls: SqlToolCallStore
    toolbox: ToolBox
    tools_ready: asyncio.Lock
    http: httpx.AsyncClient
    policy: SqlPolicyGate
    audit: AuditRepo
    notifier: Notifier
    escalation_policy: EscalationPolicy
    llm_calls: SqlLlmCallSink
    memory: MemoryService
    memory_repo: MemoryRepo
    objects: ObjectStore
    checkpointer: AsyncPostgresSaver


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
    objects = store or S3ObjectStore.from_settings(settings)
    http = httpx.AsyncClient(timeout=10)
    audit = AuditRepo(sessions)
    webhook = settings.slack_webhook_url
    key = settings.litellm_master_key.get_secret_value()
    memory_repo = MemoryRepo(sessions)
    memory = MemoryService(
        memory_repo,
        GatewayEmbedder.from_settings(http, settings),
        GatewayReranker(
            http,
            settings.litellm_url,
            key,
            fallback=LocalReranker() if settings.local_fallback else None,
        ),
        default_redactor(),
    )
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
        recorder=SqlRunRecorder(sessions, tasks, objects),
        tool_calls=SqlToolCallStore(sessions, objects),
        toolbox=ToolBox(
            settings.mcp_urls,
            settings.mcp_service_token.get_secret_value(),
            SqlArtifactBridge(sessions, objects),
        ),
        tools_ready=asyncio.Lock(),
        http=http,
        policy=SqlPolicyGate(
            sessions, PolicyClient(settings.opa_url, http), audit, settings.org_email_domain
        ),
        audit=audit,
        notifier=Notifier(
            http, webhook.get_secret_value() if webhook else None, settings.public_web_url
        ),
        escalation_policy=load_escalation_policy(settings.escalation_policy_path),
        llm_calls=SqlLlmCallSink(sessions),
        memory=memory,
        memory_repo=memory_repo,
        objects=objects,
        checkpointer=checkpointer,
    )


async def ensure_tools(deps: WorkerDeps) -> ToolBox:
    """Load MCP tool schemas once per process and mirror them into the tools table (SPEC 12.4)."""
    async with deps.tools_ready:
        if not deps.toolbox.loaded:
            await deps.toolbox.load()
            await sync_tool_registry(deps.sessions, deps.toolbox.loaded)
            try:  # OPA holds the registry as data (SPEC 15.1); the gate re-pushes if OPA restarts
                await deps.policy.push_registry()
            except httpx.HTTPError as exc:
                log.warning("could not push the tool registry to OPA", error=str(exc))
    return deps.toolbox


def graph_config(
    run: Run, budget: Budget, task: Task | None = None, settings: Settings | None = None
) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": run.thread_id}
    # Every run reads and continues from its own branch head: after a replay forks the thread,
    # the thread's newest checkpoint may belong to the replay (SPEC 19.4 step 4).
    head = run.head_checkpoint_id or (
        run.replay_from_checkpoint_id if run.kind == "replay" else None
    )
    if head:
        configurable["checkpoint_id"] = head
    config: RunnableConfig = {"configurable": configurable, "recursion_limit": budget.max_steps}
    if task is not None and settings is not None:
        handler = langchain_handler(str(run.id))
        config["callbacks"] = [NodeTimer(), *([handler] if handler else [])]
        # SPEC 19.1 trace identity: session = task (main and replays group), user = requester.
        config["metadata"] = {
            "langfuse_session_id": str(task.id),
            "langfuse_user_id": str(task.requester_id),
            "langfuse_tags": [settings.model_profile, settings.graph_version, run.kind],
            "orchestrix_run_id": str(run.id),
        }
    return config


async def build_context(deps: WorkerDeps, run: Run, task: Task) -> RunContext:
    settings = deps.settings
    defaults = {str(role): model for role, model in settings.default_models.items()}
    resolved = resolve_models(
        task.model_overrides, await admin_model_defaults(deps.sessions), defaults
    )
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
            run_id=str(run.id),
            sink=deps.llm_calls,
            team=str(task.team_id),
            local_fallback=settings.local_fallback,
        )

        async def enabled() -> set[str] | None:
            return await enabled_tools(deps.sessions)

        specialists = AgentSpecialistRunner(await ensure_tools(deps), enabled)
    services = Services(
        llm=llm,
        specialists=specialists,
        recorder=deps.recorder.for_replay() if run.kind == "replay" else deps.recorder,
        cancel=RedisCancelCheck(deps.redis),
        tool_calls=deps.tool_calls,
        policy=None if settings.fake_models else deps.policy,
        memory=None if settings.fake_models else deps.memory,
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
        side_effects="simulated" if run.kind == "replay" else "live",
        escalation_levels=dict(deps.escalation_policy.levels),
        org_email_domain=settings.org_email_domain,
        active_seconds_base=run.active_seconds,
        clarify_enabled=settings.clarify_enabled and not settings.fake_models,
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
    started = time.monotonic()
    try:
        with log_context(run_id=str(run_id)), run_trace(str(run_id)):
            await _execute(deps, run_id, lost)
    finally:
        heartbeat.cancel()
        if not lost.is_set():  # FR-63 active time: this worker's share, human waits excluded
            await deps.runs.add_active_seconds(run_id, time.monotonic() - started)
        flush_traces()


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
    config = graph_config(run, budget, task, deps.settings)
    snapshot = await deps.graph.aget_state(config)

    value: Any
    if run.kind == "replay" and run.head_checkpoint_id is None:
        # SPEC 19.4 step 3: fork from the chosen checkpoint, optionally patching state first.
        if run.replay_patch:
            forked = await deps.graph.aupdate_state(
                config, run.replay_patch, as_node=run.replay_as_node
            )
            config["configurable"] = dict(forked.get("configurable", {}))
        head = str(config.get("configurable", {}).get("checkpoint_id", ""))
        if head:  # a crash from here on continues this branch instead of forking again
            await deps.runs.set_head_checkpoint(run_id, head)
        snapshot = await deps.graph.aget_state(config)
        value = None
        await deps.events.emit(
            run_id,
            RunLifecycleEvent(type="run.started", run_id=str(run_id), reason="replay").model_dump(
                mode="json"
            ),
        )
    elif not snapshot.values:
        attachments = await deps.tasks.attachments(task.id)
        value = {
            "task": TaskInput(
                id=str(task.id),
                title=task.title,
                description=task.description,
                attachments=sorted(attachments),
                attachment_ids=attachments,
                require_plan_approval=task.require_plan_approval,
                require_final_approval=task.require_final_approval,
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

    head: str | None = None
    try:
        async for mode, chunk in deps.graph.astream(
            value,
            config,
            context=ctx,
            stream_mode=["custom", "updates", "checkpoints"],
            durability="sync",
        ):
            if lost.is_set():
                return  # another worker may own the run now: write nothing more
            if mode == "checkpoints":
                # This run's branch head (SPEC 19.4): root-namespace checkpoints only.
                cp_config: dict[str, Any] = cast(dict[str, Any], chunk).get("config") or {}
                configurable: dict[str, Any] = cp_config.get("configurable") or {}
                if not configurable.get("checkpoint_ns") and configurable.get("checkpoint_id"):
                    head = str(configurable["checkpoint_id"])
                    # Saved as it happens: a worker that dies mid-run is resumed from here,
                    # not from the checkpoint this stream started at.
                    await deps.runs.set_head_checkpoint(run_id, head)
            elif mode == "custom":
                event = cast(dict[str, Any], chunk)
                await deps.events.emit(run_id, event)
                on_event(event)
                if event.get("type") == "budget.warning":
                    await _notify_budget(deps, run_id, task, event)
    except Exception as exc:
        if lost.is_set():
            return
        log.exception("run failed")
        await _fail(deps, run, task, f"error: {type(exc).__name__}: {exc}"[:500])
        return

    if head is not None:
        config["configurable"] = {**dict(config.get("configurable", {})), "checkpoint_id": head}
    # Read the state at this run's own head: a pinned config would otherwise return the
    # checkpoint the stream started from, and after a replay fork the thread's newest
    # checkpoint may belong to another run.
    snapshot = await deps.graph.aget_state(config)

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
    if run_status != "running":
        run_finished(run_status, deps.settings.model_profile, ctx.active_seconds())


async def _notify_budget(deps: WorkerDeps, run_id: UUID, task: Task, event: dict[str, Any]) -> None:
    """budget_80 (SPEC 14.1): level notify, so a resolved row plus a notification, once per run."""
    warning = BudgetEvent.model_validate(event)
    payload = {
        "trigger": "budget_80",
        "level": "notify",
        "title": f"Run at {warning.used / warning.cap:.0%} of its {warning.metric} budget"
        if warning.cap
        else "Run near its budget",
        "task_summary": task.title,
        "proposed": {"metric": warning.metric, "used": warning.used, "cap": warning.cap},
    }
    escalation_id = await deps.escalations.record_notify(run_id, "budget_80", payload, task.team_id)
    if escalation_id is not None:
        await deps.notifier.escalation(str(escalation_id), payload)


async def _pause(deps: WorkerDeps, run_id: UUID, task: Task, interrupts: list[Any]) -> None:
    """SPEC 14.3 steps 2-3: escalation rows, task status, run paused, lease released, notify."""
    sla = timedelta(hours=deps.escalation_policy.sla_hours)
    for intr in interrupts:
        payload: dict[str, Any] = dict(intr.value)
        escalation_id = await deps.escalations.upsert_open(
            run_id, intr.id, payload, assignee_team_id=task.team_id, sla=sla
        )
        if escalation_id is not None:
            with span(
                "escalation.create",
                **{"orchestrix.trigger": payload["trigger"], "orchestrix.level": payload["level"]},
            ):
                await deps.notifier.escalation(str(escalation_id), payload)
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
    run_finished("failed", deps.settings.model_profile, run.active_seconds)


async def maintenance_llm(deps: WorkerDeps) -> GatewayChatProvider:
    """A provider for jobs outside any run (consolidation): admin/settings defaults, no run row."""
    defaults = {str(role): model for role, model in deps.settings.default_models.items()}
    models = resolve_models({}, await admin_model_defaults(deps.sessions), defaults)
    return GatewayChatProvider(
        base_url=deps.settings.litellm_url,
        api_key=deps.settings.litellm_master_key,
        models=models,
        prices=deps.settings.model_prices,
        local_fallback=deps.settings.local_fallback,
    )


async def close_deps(deps: WorkerDeps) -> None:
    await deps.http.aclose()
    await deps.pool.close()
    await deps.redis.aclose()
