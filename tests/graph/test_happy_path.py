"""The full route on fakes: plan -> parallel subtasks -> review -> synthesis -> delivery."""

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
from tests.graph.conftest import Run


async def test_fr10_fr20_fr33_happy_path(make_run, task: TaskInput) -> None:
    plan = make_plan(
        subtask("s1"),
        subtask("s2"),
        subtask("s3", "writer", ["s1", "s2"], inputs={"a": "s1.output"}),
    )
    llm = FakeChatProvider(
        planner=ScriptedChatModel(script=[plan, "# Report\nAll good."]),
        reviewer=ScriptedChatModel(
            responder=keyed_responder(
                {"s1": [approve()], "s2": [approve()], "s3": [approve()], "final": [approve(9)]}
            )
        ),
    )
    ctx, recorder = make_context(llm)
    run: Run = make_run(ctx)
    await run.start(task)

    state = await run.state()
    assert await run.interrupts() == []
    assert state["subtask_status"] == {"s1": "approved", "s2": "approved", "s3": "approved"}
    assert state["deliverable"].startswith("# Report")
    assert recorder.deliverable == state["deliverable"]
    assert recorder.statuses == ["planning", "running", "reviewing", "completed"]
    types = run.event_types()
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert types.count("subtask.started") == 3
    assert "deliverable.ready" in types
    # s3 waited for s1 and s2 and received s1's output as input
    s3_requests = [r for r in ctx.services.specialists.requests if r.subtask.key == "s3"]  # type: ignore[attr-defined]
    assert s3_requests[0].inputs == {"a": "result of s1"}
    assert state["usage"].tokens > 0
