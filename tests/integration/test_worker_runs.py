"""Worker runs on fake models against real Postgres and Redis (SPEC 8.2, 11.4, 14.3)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text

from orchestrix_core.db.models import (
    Artifact,
    Escalation,
    EscalationAction,
    Run,
    RunEvent,
    SubtaskRow,
    Task,
    ToolCall,
)
from orchestrix_core.schemas import Budget, TaskCreate
from orchestrix_worker.runner import MAX_RECLAIMS, WORKER_ID, WorkerDeps, execute_run

pytestmark = pytest.mark.integration


async def new_task(deps: WorkerDeps, **body: Any) -> tuple[uuid.UUID, uuid.UUID]:
    email = f"{uuid.uuid4().hex[:8]}@test.local"
    user = await deps_users(deps).upsert(f"dev|{email}", email, email)
    team = await deps_users(deps).ensure_team_member(user.id, "integration")
    created = await deps.tasks.create(
        requester_id=user.id,
        team_id=team.id,
        body=TaskCreate(title="Latency report", description="Summarise latency.", **body),
        budget=Budget(),
        idempotency_key=None,
        graph_version="test",
    )
    return created.task_id, created.run_id


def deps_users(deps: WorkerDeps):
    from orchestrix_core.db.repos.tasks import UserRepo

    return UserRepo(deps.sessions)


async def scalar(deps: WorkerDeps, stmt: Any) -> Any:
    async with deps.sessions() as s:
        return (await s.execute(stmt)).scalar()


async def event_types(deps: WorkerDeps, run_id: uuid.UUID) -> list[str]:
    async with deps.sessions() as s:
        rows = await s.execute(
            select(RunEvent.type).where(RunEvent.run_id == run_id).order_by(RunEvent.id)
        )
        return list(rows.scalars())


async def test_fr03_run_completes_end_to_end_on_fake_models(worker_deps: WorkerDeps, store) -> None:
    deps = worker_deps
    task_id, run_id = await new_task(deps)
    await execute_run(deps, run_id)

    task = await scalar(deps, select(Task).where(Task.id == task_id))
    run = await scalar(deps, select(Run).where(Run.id == run_id))
    assert task.status == "completed"
    assert (run.status, run.lease_owner, run.ended_at is not None) == ("completed", None, True)
    assert run.model_config_ == {
        "planner": "gpt-oss-120b",
        "specialist": "gpt-oss-120b",
        "reviewer": "gpt-oss-120b",
        "fast": "gpt-oss-120b",
    }
    assert run.head_checkpoint_id
    types = await event_types(deps, run_id)
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert (
        await scalar(
            deps, select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run_id)
        )
        == 3
    )
    statuses = await scalar(
        deps, select(func.array_agg(SubtaskRow.status)).where(SubtaskRow.run_id == run_id)
    )
    assert sorted(statuses) == ["approved", "approved", "approved"]
    artifact = await scalar(deps, select(Artifact).where(Artifact.run_id == run_id))
    assert task.final_artifact_id == artifact.id
    assert store.objects[artifact.object_key].startswith(b"# Report")
    published = await scalar(
        deps, select(func.bool_and(RunEvent.published)).where(RunEvent.run_id == run_id)
    )
    assert published is True


async def test_fr42_pause_then_resume_with_posted_decision(worker_deps: WorkerDeps) -> None:
    deps = worker_deps
    task_id, run_id = await new_task(deps, require_plan_approval=True)
    await execute_run(deps, run_id)

    assert (
        await scalar(deps, select(Task.status).where(Task.id == task_id))
    ) == "awaiting_plan_approval"
    run = await scalar(deps, select(Run).where(Run.id == run_id))
    assert (run.status, run.lease_owner) == ("paused", None)  # no worker holds a paused run
    esc = await scalar(deps, select(Escalation).where(Escalation.run_id == run_id))
    assert (esc.trigger, esc.level, esc.status) == (
        "plan_approval_required",
        "approve_plan",
        "open",
    )
    assert "escalation.created" in await event_types(deps, run_id)

    # Running again without a decision changes nothing.
    await execute_run(deps, run_id)
    assert (await scalar(deps, select(Run.status).where(Run.id == run_id))) == "paused"

    # What the M4 decision API will write: an action, and the escalation marked resolved.
    user_id = await scalar(deps, select(Task.requester_id).where(Task.id == task_id))
    async with deps.sessions.begin() as s:
        s.add(
            EscalationAction(
                escalation_id=esc.id,
                actor_id=user_id,
                action="approve",
                body={"action": "approve", "actor_id": str(user_id)},
            )
        )
        await s.execute(
            text("UPDATE escalations SET status = 'resolved' WHERE id = :id"), {"id": esc.id}
        )
    await execute_run(deps, run_id)

    assert (await scalar(deps, select(Task.status).where(Task.id == task_id))) == "completed"
    types = await event_types(deps, run_id)
    assert "run.resumed" in types
    assert "plan.approved" in types
    assert types[-1] == "run.completed"


async def test_nfr01_lease_is_exclusive_and_heartbeat_extends_it(worker_deps: WorkerDeps) -> None:
    deps = worker_deps
    _, run_id = await new_task(deps)
    assert await deps.runs.acquire_lease(run_id, "worker-a", 60)
    assert not await deps.runs.acquire_lease(run_id, "worker-b", 60)
    assert await deps.runs.heartbeat(run_id, "worker-a", 60)
    assert not await deps.runs.heartbeat(run_id, "worker-b", 60)  # b never held it
    assert await deps.runs.release(run_id, "worker-a", "paused")
    assert await deps.runs.acquire_lease(run_id, "worker-b", 60)


async def test_nfr01_reaper_reclaims_expired_lease(worker_deps: WorkerDeps) -> None:
    deps = worker_deps
    _, run_id = await new_task(deps)
    assert await deps.runs.acquire_lease(run_id, "dead-worker", 60)
    async with deps.sessions.begin() as s:
        await s.execute(
            text("UPDATE runs SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
            {"id": run_id},
        )

    reclaimed = {r.run_id: r.reclaim_count for r in await deps.runs.reap_expired()}
    assert reclaimed[run_id] == 1
    assert not await deps.runs.heartbeat(run_id, "dead-worker", 60)  # the old owner lost it
    await execute_run(deps, run_id)  # what resume_run does after the reaper enqueues it
    assert (await scalar(deps, select(Run.status).where(Run.id == run_id))) == "completed"


async def test_crash_loop_guard_fails_run_after_three_reclaims(worker_deps: WorkerDeps) -> None:
    deps = worker_deps
    task_id, run_id = await new_task(deps)
    async with deps.sessions.begin() as s:
        await s.execute(
            text("UPDATE runs SET reclaim_count = :n WHERE id = :id"),
            {"n": MAX_RECLAIMS, "id": run_id},
        )
    await execute_run(deps, run_id)

    task = await scalar(deps, select(Task).where(Task.id == task_id))
    assert (task.status, task.failure_reason) == ("failed", "crash_loop")
    assert (await scalar(deps, select(Run.status).where(Run.id == run_id))) == "failed"
    assert (await event_types(deps, run_id))[-1] == "run.failed"


async def test_lease_held_elsewhere_means_skip(worker_deps: WorkerDeps) -> None:
    deps = worker_deps
    task_id, run_id = await new_task(deps)
    assert await deps.runs.acquire_lease(run_id, "other-worker", 60)
    await execute_run(deps, run_id)
    assert (await scalar(deps, select(Task.status).where(Task.id == task_id))) == "queued"
    assert WORKER_ID != "other-worker"


async def test_outbox_relay_publishes_missed_events(worker_deps: WorkerDeps) -> None:
    deps = worker_deps
    _, run_id = await new_task(deps)
    async with deps.sessions.begin() as s:
        s.add(
            RunEvent(
                run_id=run_id,
                type="run.started",
                data={"type": "run.started", "run_id": str(run_id)},
                published=False,
            )
        )
        await s.flush()
        await s.execute(
            text(
                "UPDATE run_events SET created_at = now() - interval '1 minute' WHERE run_id = :id"
            ),
            {"id": run_id},
        )
    pubsub = deps.redis.pubsub()
    await pubsub.subscribe(f"run:{run_id}")
    assert await deps.events.relay_unpublished() >= 1
    message = None
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if message:
            break
    await pubsub.aclose()
    assert message is not None
    assert b"run.started" in message["data"]
    assert (
        await scalar(
            deps, select(func.bool_and(RunEvent.published)).where(RunEvent.run_id == run_id)
        )
        is True
    )
