"""Periodic jobs (SPEC 8.1): lease reaper, outbox relay, idempotency-key retention.

Run the schedule with `taskiq scheduler orchestrix_worker.scheduler:scheduler`; the jobs
themselves execute on the workers.
"""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from orchestrix_core.logging import get_logger
from orchestrix_core.queue import RESUME_RUN, enqueue
from orchestrix_worker.broker import broker
from orchestrix_worker.jobs import resume_run, start_run
from orchestrix_worker.runner import get_deps

log = get_logger(__name__)

__all__ = ["reap_leases", "relay_events", "resume_run", "scheduler", "start_run"]


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
        await enqueue(RESUME_RUN, str(item.run_id))
    return len(reclaimed)


@broker.task(task_name="orchestrix.relay_events", schedule=[{"interval": 10}])
async def relay_events() -> int:
    """Publish run_events rows whose live Redis publish failed (SPEC 17.2)."""
    return await (await get_deps()).events.relay_unpublished()


@broker.task(task_name="orchestrix.expire_idempotency_keys", schedule=[{"interval": 3600}])
async def expire_idempotency_keys() -> int:
    """Plan improvement 12: free Idempotency-Keys after the FR-05 24 h window."""
    return await (await get_deps()).tasks.clear_expired_idempotency_keys()


scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
