"""Periodic jobs (SPEC 8.1): lease reaper, outbox relay, idempotency-key retention.

Run the schedule with `taskiq scheduler orchestrix_worker.scheduler:scheduler`; the jobs
themselves execute on the workers.
"""

from datetime import timedelta

from redis.exceptions import RedisError
from sqlalchemy import func, select
from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from orchestrix_core import metrics
from orchestrix_core.db.models import Escalation
from orchestrix_core.logging import get_logger
from orchestrix_core.queue import RESUME_RUN, enqueue
from orchestrix_core.schemas.events import RunLifecycleEvent
from orchestrix_core.state_machine import TERMINAL
from orchestrix_worker.broker import broker
from orchestrix_worker.jobs import resume_run, start_run
from orchestrix_worker.runner import get_deps, maintenance_llm
from orchestrix_worker.services import apply_retention

log = get_logger(__name__)

__all__ = [
    "check_escalation_sla",
    "memory_backfill",
    "memory_consolidate",
    "reap_leases",
    "refresh_gauges",
    "relay_events",
    "resume_run",
    "retention",
    "scheduler",
    "start_run",
]


@broker.task(task_name="orchestrix.reap_leases", schedule=[{"interval": 30}])
async def reap_leases() -> int:
    """SPEC 8.2 step 4: re-enqueue runs whose worker died (the crash-loop guard runs on resume)."""
    deps = await get_deps()
    reclaimed = await deps.runs.reap_expired()
    for item in reclaimed:
        log.warning(
            "reclaiming run with an expired lease",
            run_id=str(item.run_id),
            reclaims=item.reclaim_count,
        )
        metrics.LEASE_RECLAIMS.inc()
        await enqueue(RESUME_RUN, str(item.run_id))
    return len(reclaimed)


@broker.task(task_name="orchestrix.refresh_gauges", schedule=[{"interval": 30}])
async def refresh_gauges() -> None:
    """escalations_open{level} and queue_depth{job} (SPEC 19.2), refreshed for /metrics."""
    deps = await get_deps()
    async with deps.sessions() as s:
        rows = (
            await s.execute(
                select(Escalation.level, func.count())
                .where(Escalation.status == "open")
                .group_by(Escalation.level)
            )
        ).all()
        overdue = (
            await s.execute(
                select(func.count())
                .select_from(Escalation)
                .where(Escalation.status == "open", Escalation.due_at < func.now())
            )
        ).scalar_one()
    metrics.ESCALATIONS_OVERDUE.set(int(overdue))
    counts = {str(level): int(n) for level, n in rows}
    for level in metrics.LEVELS:
        metrics.ESCALATIONS_OPEN.labels(level).set(counts.get(level, 0))
    try:
        depth = int(await deps.redis.xlen("orchestrix"))  # pyright: ignore[reportUnknownMemberType]
    except RedisError:
        return
    metrics.QUEUE_DEPTH.labels("all").set(depth)


@broker.task(task_name="orchestrix.relay_events", schedule=[{"interval": 10}])
async def relay_events() -> int:
    """Publish run_events rows whose live Redis publish failed (SPEC 17.2)."""
    return await (await get_deps()).events.relay_unpublished()


@broker.task(task_name="orchestrix.expire_idempotency_keys", schedule=[{"interval": 3600}])
async def expire_idempotency_keys() -> int:
    """Plan improvement 12: free Idempotency-Keys after the FR-05 24 h window."""
    return await (await get_deps()).tasks.clear_expired_idempotency_keys()


@broker.task(task_name="orchestrix.escalation_sla", schedule=[{"interval": 15 * 60}])
async def check_escalation_sla() -> int:
    """SPEC 14.5, FR-45: re-notify past due_at; after the timeout, fail the task with
    escalation_timeout (after a final notification)."""
    deps = await get_deps()
    policy = deps.escalation_policy
    for esc in await deps.escalations.overdue(timedelta(hours=policy.sla_hours)):
        await deps.notifier.escalation(str(esc.id), esc.payload, reminder=True)
    expired = await deps.escalations.expire(timedelta(hours=policy.timeout_hours))
    for esc in expired:
        await deps.notifier.send(
            f"Escalation expired; the task fails: {esc.payload.get('title', '')} "
            f"<{deps.notifier.link(str(esc.id))}|open>"
        )
        run = await deps.runs.get(esc.run_id)
        task = await deps.tasks.get(run.task_id) if run else None
        if run is None or task is None or task.status in TERMINAL:
            continue
        await deps.tasks.set_status(task.id, "failed", "escalation_timeout")
        await deps.runs.set_status(run.id, "failed")
        await deps.events.emit(
            run.id,
            RunLifecycleEvent(
                type="run.failed", run_id=str(run.id), reason="escalation_timeout"
            ).model_dump(mode="json"),
        )
    return len(expired)


@broker.task(task_name="orchestrix.memory_backfill", schedule=[{"interval": 600}])
async def memory_backfill() -> int:
    """A5: embed memories written while the embedding endpoint (VPN) was unreachable."""
    return await (await get_deps()).memory.backfill()


@broker.task(task_name="orchestrix.memory_consolidate", schedule=[{"cron": "15 3 * * *"}])
async def memory_consolidate() -> int:
    """FR-54: nightly merge of near-duplicate memories (cosine >= 0.92)."""
    deps = await get_deps()
    return await deps.memory.consolidate(await maintenance_llm(deps))


@broker.task(task_name="orchestrix.retention", schedule=[{"cron": "45 3 * * *"}])
async def retention() -> dict[str, int]:
    """NFR-06: traces 30 days, run checkpoints 180 days (settings)."""
    deps = await get_deps()
    return await apply_retention(
        deps.sessions,
        deps.objects,
        deps.checkpointer,
        trace_days=deps.settings.trace_retention_days,
        run_days=deps.settings.run_retention_days,
    )


scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
