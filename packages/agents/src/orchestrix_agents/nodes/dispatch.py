"""dispatch: schedule ready subtasks (FR-20) after cancel and budget checks (FR-04, FR-63)."""

from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Send

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, ask_human, emit, plan_progress, require_plan
from orchestrix_agents.state import DONE_OK, TERMINAL, RunState, SubtaskStatus, WorkerState
from orchestrix_core.budgets import check_budget
from orchestrix_core.schemas import (
    Budget,
    EscalationPayload,
    EscalationRecord,
    Plan,
)
from orchestrix_core.schemas.events import BudgetEvent, SubtaskEvent


def cascade_skips(plan: Plan, statuses: dict[str, SubtaskStatus]) -> dict[str, SubtaskStatus]:
    """Skip every pending subtask that depends (transitively) on a failed or skipped one."""
    out = dict(statuses)
    changed = True
    while changed:
        changed = False
        for s in plan.subtasks:
            if out.get(s.key) == "pending" and any(
                out.get(d) in ("failed", "skipped") for d in s.depends_on
            ):
                out[s.key] = "skipped"
                changed = True
    return out


def ready_subtasks(plan: Plan, statuses: dict[str, SubtaskStatus]) -> list[str]:
    return [
        s.key
        for s in plan.subtasks
        if statuses.get(s.key) == "pending"
        and all(statuses.get(d) in DONE_OK for d in s.depends_on)
    ]


async def dispatch(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    plan = require_plan(state)

    if await ctx.services.cancel.is_cancelled(ctx.run_id):
        return {"stop_reason": "cancelled", "usage": STEP}

    update: dict[str, Any] = {"usage": STEP}
    budget = state.get("budget", ctx.budget)
    status = check_budget(state.get("usage") or STEP, budget)
    if status.level == "exceeded":
        # budget_100 (SPEC 14.1): raise the caps or stop. Asked before any write in this node.
        decision = ask_human(
            EscalationPayload(
                trigger="budget_100",
                level="approve_action",
                title=f"Budget cap reached on {status.metric}: raise it or stop",
                task_summary=state["task"].title,
                plan_progress=plan_progress(plan, dict(state.get("subtask_status", {}))),
                proposed={
                    "budget": budget.model_dump(),
                    "metric": status.metric,
                    "used": status.used,
                },
                reasoning=f"{status.metric} used {status.used} of {status.cap}",
                related_memories=[],
                allowed_actions=["approve_with_edits", "reject"],
            )
        )
        record = EscalationRecord(
            trigger="budget_100", level="approve_action", resolution=decision.action
        )
        if decision.action == "reject":
            return {"stop_reason": "budget_exceeded", "escalations": [record], "usage": STEP}
        budget = Budget.model_validate({**budget.model_dump(), **(decision.edits or {})})
        update |= {"budget": budget, "escalations": [record]}
    elif status.level == "warning":
        emit(
            runtime,
            BudgetEvent(
                type="budget.warning",
                run_id=ctx.run_id,
                metric=status.metric or "",
                used=status.used,
                cap=status.cap,
            ),
        )

    await ctx.services.recorder.set_task_status(ctx.task_id, "running")
    statuses: dict[str, SubtaskStatus] = {
        s.key: state.get("subtask_status", {}).get(s.key, "pending") for s in plan.subtasks
    }
    statuses = cascade_skips(plan, statuses)
    for key in ready_subtasks(plan, statuses)[: ctx.max_concurrency]:
        statuses[key] = "running"

    by_key = {s.key: s for s in plan.subtasks}
    previous = state.get("subtask_status", {})
    for key, value in statuses.items():
        if previous.get(key) != value:
            await ctx.services.recorder.save_subtask(
                ctx.run_id, state.get("plan_version", 1), by_key[key], value
            )
            if value == "running":
                emit(
                    runtime,
                    SubtaskEvent(
                        type="subtask.started",
                        run_id=ctx.run_id,
                        subtask=key,
                        specialist=by_key[key].specialist,
                    ),
                )
            else:
                emit(
                    runtime,
                    SubtaskEvent(
                        type="subtask.status", run_id=ctx.run_id, subtask=key, status=value
                    ),
                )
    return update | {"subtask_status": statuses}


def resolve_inputs(state: RunState, key: str) -> dict[str, str]:
    subtask = next(s for s in require_plan(state).subtasks if s.key == key)
    results = state.get("subtask_results", {})
    resolved: dict[str, str] = {}
    for name, ref in subtask.inputs.items():
        if ref.endswith(".output"):
            dep = ref.removesuffix(".output")
            resolved[name] = results[dep].output if dep in results else ""
        else:
            resolved[name] = f"[{ref}: read it with files.read_artifact]"
    return resolved


def build_worker_input(state: RunState, key: str) -> WorkerState:
    subtask = next(s for s in require_plan(state).subtasks if s.key == key)
    return {
        "subtask": subtask,
        "plan_version": state.get("plan_version", 1),
        "inputs": resolve_inputs(state, key),
        "memories": state.get("memories", []),
        "attempts": 0,
        "revisions": 0,
        "feedback": "",
        "hint": "",
    }


def route_dispatch(state: RunState) -> list[Send] | str:
    if state.get("stop_reason"):
        return "finalize"
    statuses = state.get("subtask_status", {})
    running = [k for k, s in statuses.items() if s == "running"]
    if running:
        return [Send("subtask_worker", build_worker_input(state, k)) for k in running]
    if all(s in TERMINAL for s in statuses.values()):
        return "synthesize"
    raise RuntimeError(f"dispatch made no progress: {statuses}")  # unreachable after cascade_skips
