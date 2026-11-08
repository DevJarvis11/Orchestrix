"""Taskiq jobs (SPEC 9): start_run and resume_run share one leased execution path."""

from uuid import UUID

from taskiq import TaskiqEvents, TaskiqState

from orchestrix_core.config import get_settings
from orchestrix_core.logging import configure_logging
from orchestrix_core.queue import REPLAY_RUN, RESUME_RUN, START_RUN
from orchestrix_worker.broker import broker
from orchestrix_worker.observability import start_process
from orchestrix_worker.runner import execute_run, get_deps

configure_logging()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def worker_startup(state: TaskiqState) -> None:
    """Worker processes only (not the scheduler): tracing, /metrics (SPEC 19), and the embedding
    guard (SPEC 13.5): refuse to start if the stored embedding model differs from settings."""
    settings = get_settings()
    start_process(settings)
    deps = await get_deps()
    await deps.memory_repo.check_embedding_model(settings.embedding_model, settings.embedding_dim)


@broker.task(task_name=START_RUN)
async def start_run(run_id: str) -> None:
    await execute_run(await get_deps(), UUID(run_id))


@broker.task(task_name=RESUME_RUN)
async def resume_run(run_id: str) -> None:
    """Continue from the last checkpoint: after a crash, or once escalation decisions are posted."""
    await execute_run(await get_deps(), UUID(run_id))


@broker.task(task_name=REPLAY_RUN)
async def replay_run(run_id: str) -> None:
    """SPEC 19.4: a replay run forks its thread at a checkpoint; simulated side effects."""
    await execute_run(await get_deps(), UUID(run_id))
