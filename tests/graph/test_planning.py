"""Planning routes (SPEC 11.3-11.4): repair, human fix after two failures, approval gates."""

import pytest

from orchestrix_core.schemas import TaskInput
from tests.fixtures.fakes import (
    FakeChatProvider,
    approve,
    keyed_responder,
    make_context,
    make_plan,
    subtask,
)
from tests.fixtures.scripted_model import ScriptedChatModel

CYCLIC = make_plan(subtask("s1", depends_on=["s2"]), subtask("s2", depends_on=["s1"]))
VALID = make_plan(subtask("s1"))
LOW = make_plan(subtask("s1"), confidence=0.4)


def reviewer() -> ScriptedChatModel:
    return ScriptedChatModel(responder=keyed_responder({"s1": [approve()], "final": [approve()]}))


def decision(action: str, **kw: object) -> dict[str, object]:
    return {"action": action, "actor_id": "reviewer-1", **kw}


async def test_fr11_invalid_plan_repaired_once(make_run, task: TaskInput) -> None:
    planner = ScriptedChatModel(script=[CYCLIC, VALID, "deliverable"])
    run = make_run(make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))[0])
    await run.start(task)

    assert "plan.invalid" in run.event_types()
    assert run.event_types()[-1] == "run.completed"
    repair_prompt = str(planner.calls[1][0].content)
    assert "dependency cycle" in repair_prompt
    assert (await run.state())["plan_version"] == 2


async def test_fr11_invalid_twice_goes_to_human_take_over(make_run, task: TaskInput) -> None:
    planner = ScriptedChatModel(script=[CYCLIC, CYCLIC, "deliverable"])
    ctx, recorder = make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))
    run = make_run(ctx)
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "plan_invalid_twice"
    assert pending.value["level"] == "take_over"
    await run.resume(decision("approve_with_edits", edits=VALID.model_dump(mode="json")))

    assert "plan.edited" in run.event_types()
    assert run.event_types()[-1] == "run.completed"
    assert recorder.plans[3] == VALID  # versions 1, 2 were the invalid attempts


async def test_fr11_edited_plan_still_invalid_asks_again(make_run, task: TaskInput) -> None:
    planner = ScriptedChatModel(script=[CYCLIC, CYCLIC, "deliverable"])
    run = make_run(make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))[0])
    await run.start(task)
    await run.resume(decision("approve_with_edits", edits=CYCLIC.model_dump(mode="json")))

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "plan_invalid_twice"
    await run.resume(decision("take_over", output=VALID.model_dump_json()))
    assert run.event_types()[-1] == "run.completed"


async def test_fr11_unparseable_planner_output_is_repaired(make_run, task: TaskInput) -> None:
    # Two junk replies exhaust the structured-output retry; the repair prompt then succeeds.
    planner = ScriptedChatModel(script=["not json", "still not json", VALID, "deliverable"])
    run = make_run(make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))[0])
    await run.start(task)
    assert "plan.invalid" in run.event_types()
    assert run.event_types()[-1] == "run.completed"


async def test_fr13_low_confidence_escalates_approve_plan(make_run, task: TaskInput) -> None:
    planner = ScriptedChatModel(script=[LOW, "deliverable"])
    run = make_run(make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))[0])
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "plan_low_confidence"
    assert pending.value["level"] == "approve_plan"
    assert "subtask.started" not in run.event_types()  # paused before any subtask runs
    await run.resume(decision("approve"))
    assert "plan.approved" in run.event_types()
    assert run.event_types()[-1] == "run.completed"


@pytest.mark.parametrize("flag", [True])
async def test_fr02_plan_approval_flag_reject_fails_run(make_run, flag: bool) -> None:
    task = TaskInput(id="task-1", title="t", description="d", require_plan_approval=flag)
    planner = ScriptedChatModel(script=[VALID])
    ctx, recorder = make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))
    run = make_run(ctx)
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "plan_approval_required"
    await run.resume(decision("reject", reason="wrong approach"))
    assert run.event_types()[-1] == "run.failed"
    assert recorder.statuses[-1] == "failed"
    assert (await run.state())["escalations"][-1].resolution == "reject"


async def test_fr15_clarify_then_low_confidence_gate_then_completes(
    make_run, task: TaskInput
) -> None:
    """Two human pauses in a row (clarify, then the plan gate): each resume returns the task
    to the phase it was in, so every FR-01 transition stays legal (the fake enforces them)."""
    from dataclasses import replace

    from orchestrix_core.schemas import Clarity

    planner = ScriptedChatModel(
        script=[Clarity(ambiguous=True, questions=["Which audience?"]), LOW, "deliverable"]
    )
    ctx, recorder = make_context(FakeChatProvider(planner=planner, reviewer=reviewer()))
    run = make_run(replace(ctx, clarify_enabled=True))
    await run.start(task)

    [pending] = await run.interrupts()
    assert (pending.value["trigger"], pending.value["level"]) == ("ambiguous_request", "clarify")
    await run.resume(decision("answer", answers=["Platform engineers"]))

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "plan_low_confidence"
    await run.resume(decision("approve"))
    assert run.event_types()[-1] == "run.completed"
    assert recorder.statuses[-1] == "completed"
    assert "Platform engineers" in str(planner.calls[1][0].content)  # answers reach the planner
