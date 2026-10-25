"""Taskiq jobs (SPEC 9): start_run and resume_run share one leased execution path."""

from uuid import UUID

from orchestrix_core.logging import configure_logging
from orchestrix_core.queue import RESUME_RUN, START_RUN
from orchestrix_worker.broker import broker
from orchestrix_worker.runner import execute_run, get_deps

configure_logging()


@broker.task(task_name=START_RUN)
async def start_run(run_id: str) -> None:
    await execute_run(await get_deps(), UUID(run_id))


@broker.task(task_name=RESUME_RUN)
async def resume_run(run_id: str) -> None:
    """Continue from the last checkpoint: after a crash, or once escalation decisions are posted."""
    await execute_run(await get_deps(), UUID(run_id))
