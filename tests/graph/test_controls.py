"""Run controls: cancel (FR-04), budgets (FR-63), final review (FR-33), best-effort memory."""

import sys

from orchestrix_core.schemas import Budget, TaskInput
from tests.fixtures.fakes import (
    FakeCancel,
    FakeChatProvider,
    FakeSpecialists,
    approve,
    keyed_responder,
    make_context,
    make_plan,
    revise,
    subtask,
)
from tests.fixtures.scripted_model import ScriptedChatModel


def decision(action: str, **kw: object) -> dict[str, object]:
    return {"action": action, "actor_id": "reviewer-1", **kw}


def provider(final: list, planner_replies: int = 3) -> FakeChatProvider:
    plan = make_plan(subtask("s1"))
    return FakeChatProvider(
        planner=ScriptedChatModel(
            script=[plan, *[f"deliverable v{i}" for i in range(1, planner_replies)]]
        ),
        reviewer=ScriptedChatModel(responder=keyed_responder({"s1": [approve()], "final": final})),
    )


async def test_fr04_cancel_stops_before_subtasks(make_run, task: TaskInput) -> None:
    specialists = FakeSpecialists()
    ctx, recorder = make_context(
        provider([approve()]), specialists, cancel=FakeCancel(cancelled=True)
    )
    run = make_run(ctx)
    await run.start(task)

    assert run.event_types()[-1] == "run.cancelled"
    assert recorder.statuses[-1] == "cancelled"
    assert specialists.requests == []


async def test_fr63_budget_cap_escalates_and_reject_stops(make_run, task: TaskInput) -> None:
    ctx, recorder = make_context(provider([approve()]), budget=Budget(max_tokens=100))
    run = make_run(ctx)
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "budget_100"
    assert pending.value["level"] == "approve_action"
    await run.resume(decision("reject", reason="too expensive"))
    assert run.event_types()[-1] == "run.failed"
    assert recorder.statuses[-1] == "budget_exceeded"


async def test_fr63_budget_raised_by_human_continues(make_run, task: TaskInput) -> None:
    run = make_run(make_context(provider([approve()]), budget=Budget(max_tokens=100))[0])
    await run.start(task)
    await run.resume(decision("approve_with_edits", edits={"max_tokens": 1_000_000}))

    state = await run.state()
    assert state["budget"].max_tokens == 1_000_000
    assert run.event_types()[-1] == "run.completed"


async def test_fr63_budget_warning_at_80_percent(make_run, task: TaskInput) -> None:
    # The planner call uses 150 tokens; a 180-token cap puts the run at 83% at dispatch.
    run = make_run(make_context(provider([approve()]), budget=Budget(max_tokens=180))[0])
    await run.start(task)
    assert "budget.warning" in run.event_types()


async def test_fr33_final_review_revises_once(make_run, task: TaskInput) -> None:
    ctx, _ = make_context(provider([revise("add a summary"), approve()]))
    run = make_run(ctx)
    await run.start(task)

    state = await run.state()
    assert state["final_revisions"] == 1
    assert state["deliverable"] == "deliverable v2"
    resynth_prompt = str(ctx.services.llm.chat("planner").calls[-1][0].content)  # type: ignore[attr-defined]
    assert "add a summary" in resynth_prompt
    assert run.event_types()[-1] == "run.completed"


async def test_fr33_final_review_second_failure_escalates(make_run, task: TaskInput) -> None:
    run = make_run(make_context(provider([revise(), revise()]))[0])
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "low_review_score"
    await run.resume(decision("take_over", output="human deliverable"))
    assert (await run.state())["deliverable"] == "human deliverable"
    assert run.event_types()[-1] == "run.completed"


async def test_fr51_write_memory_failure_never_fails_run(
    make_run, task: TaskInput, monkeypatch
) -> None:
    def broken_emit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("memory store down")

    module = sys.modules[
        "orchestrix_agents.nodes.write_memory"
    ]  # the package re-exports the function
    monkeypatch.setattr(module, "emit", broken_emit)
    run = make_run(make_context(provider([approve()]))[0])
    await run.start(task)

    state = await run.state()
    assert any(e.node == "write_memory" for e in state["errors"])
    assert run.event_types()[-1] == "run.completed"
