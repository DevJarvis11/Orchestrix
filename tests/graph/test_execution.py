"""Subtask worker routes (SPEC 11.5): retries, revise loop, escalations, skip cascade, fan-out."""

import asyncio
from dataclasses import dataclass

import pytest

from orchestrix_agents.context import (
    RunContext,
    SpecialistError,
    SpecialistOutcome,
    SpecialistRequest,
)
from orchestrix_agents.nodes.dispatch import build_worker_input
from orchestrix_core.schemas import SubtaskResult, TaskInput
from tests.fixtures.fakes import (
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


def fail(msg: str = "boom") -> SpecialistError:
    return SpecialistError(msg, retryable=False)


def models(plan, reviews: dict[str, list]) -> FakeChatProvider:
    reviews.setdefault("final", [approve()])
    return FakeChatProvider(
        planner=ScriptedChatModel(script=[plan, "deliverable", "deliverable"]),
        reviewer=ScriptedChatModel(responder=keyed_responder(reviews)),
    )


async def test_fr24_one_failure_retries_with_alternative_hint(make_run, task: TaskInput) -> None:
    specialists = FakeSpecialists(outcomes={"s1": [fail()]})
    ctx, _ = make_context(models(make_plan(subtask("s1")), {"s1": [approve()]}), specialists)
    run = make_run(ctx)
    await run.start(task)

    assert (await run.state())["subtask_status"] == {"s1": "approved"}
    assert specialists.requests[0].hint == ""
    assert "different method" in specialists.requests[1].hint


def test_fr25_taint_propagates_from_a_fetched_subtask_to_its_dependent(task: TaskInput) -> None:
    plan = make_plan(
        subtask("s1"), subtask("s2", depends_on=["s1"], inputs={"research": "s1.output"})
    )
    worker = build_worker_input(
        {
            "task": task,
            "plan": plan,
            "subtask_results": {
                "s1": SubtaskResult(key="s1", output="untrusted page summary", confidence=0.8)
            },
            "tainted": {"s1": True},
        },
        "s2",
    )
    assert worker.get("tainted_inputs") is True
    assert worker.get("inputs", {})["research"] == "untrusted page summary"


async def test_fr24_failure_twice_escalates_take_over(make_run, task: TaskInput) -> None:
    specialists = FakeSpecialists(outcomes={"s1": [fail(), fail()]})
    ctx, recorder = make_context(models(make_plan(subtask("s1")), {"s1": [approve()]}), specialists)
    run = make_run(ctx)
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "subtask_failed_twice"
    assert pending.value["level"] == "take_over"
    assert pending.value["subtask"] == "s1"
    await run.resume(decision("take_over", output="human-written s1"))

    state = await run.state()
    assert state["subtask_status"] == {"s1": "taken_over"}
    assert state["subtask_results"]["s1"].output == "human-written s1"
    assert recorder.subtasks["s1"][-1] == "taken_over"
    assert run.event_types()[-1] == "run.completed"


async def test_fr31_revise_loop_max_two(make_run, task: TaskInput) -> None:
    specialists = FakeSpecialists()
    reviews = {"s1": [revise("add sources"), revise("tighten"), approve()]}
    run = make_run(make_context(models(make_plan(subtask("s1")), reviews), specialists)[0])
    await run.start(task)

    state = await run.state()
    assert state["subtask_status"] == {"s1": "approved"}
    assert [r.verdict for r in state["reviews"]["s1"]] == ["revise", "revise", "approve"]
    assert [r.feedback for r in specialists.requests] == ["", "add sources", "tighten"]


async def test_fr31_third_revise_escalates_low_review_score(make_run, task: TaskInput) -> None:
    reviews = {"s1": [revise(), revise(), revise()]}
    run = make_run(make_context(models(make_plan(subtask("s1")), reviews))[0])
    await run.start(task)

    [pending] = await run.interrupts()
    assert pending.value["trigger"] == "low_review_score"
    assert pending.value["level"] == "approve_action"
    await run.resume(decision("approve"))
    assert (await run.state())["subtask_status"] == {"s1": "approved"}


async def test_fr30_approve_below_threshold_is_revised(make_run, task: TaskInput) -> None:
    reviews = {"s1": [approve(score=4.0), approve(score=8.0)]}
    run = make_run(make_context(models(make_plan(subtask("s1")), reviews))[0])
    await run.start(task)
    assert len((await run.state())["reviews"]["s1"]) == 2


async def test_fr43_reject_retry_reruns_with_human_note(make_run, task: TaskInput) -> None:
    specialists = FakeSpecialists(outcomes={"s1": [fail(), fail()]})
    run = make_run(
        make_context(models(make_plan(subtask("s1")), {"s1": [approve()]}), specialists)[0]
    )
    await run.start(task)
    await run.resume(decision("reject", reason="use the internal wiki", on_reject="retry"))

    assert (await run.state())["subtask_status"] == {"s1": "approved"}
    assert specialists.requests[-1].feedback == "use the internal wiki"


async def test_fr43_reject_skip_cascades_to_dependents(make_run, task: TaskInput) -> None:
    plan = make_plan(
        subtask("s1"),
        subtask("s2", depends_on=["s1"]),
        subtask("s3", depends_on=["s2"]),
        subtask("s4"),
    )
    specialists = FakeSpecialists(outcomes={"s1": [fail(), fail()]})
    reviews = {"s4": [approve()]}
    ctx, _ = make_context(models(plan, reviews), specialists)
    run = make_run(ctx)
    await run.start(task)
    await run.resume(decision("reject", reason="not needed", on_reject="skip"))

    state = await run.state()
    assert state["subtask_status"] == {
        "s1": "skipped",
        "s2": "skipped",
        "s3": "skipped",
        "s4": "approved",
    }
    assert run.event_types()[-1] == "run.completed"
    synth_prompt = str(ctx.services.llm.chat("planner").calls[-1][0].content)  # type: ignore[attr-defined]
    assert "Skipped subtasks: s1, s2, s3" in synth_prompt


async def test_fr43_reject_fail_task_stops_run(make_run, task: TaskInput) -> None:
    specialists = FakeSpecialists(outcomes={"s1": [fail(), fail()]})
    ctx, recorder = make_context(models(make_plan(subtask("s1")), {}), specialists)
    run = make_run(ctx)
    await run.start(task)
    await run.resume(decision("reject", reason="abandon", on_reject="fail_task"))
    assert run.event_types()[-1] == "run.failed"
    assert recorder.statuses[-1] == "failed"


@dataclass
class ConcurrencyProbe:
    in_flight: int = 0
    peak: int = 0

    async def run(self, request: SpecialistRequest, ctx: RunContext) -> SpecialistOutcome:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return SpecialistOutcome(
            result=SubtaskResult(key=request.subtask.key, output="ok", confidence=1.0)
        )


async def test_fr20_parallel_fan_out_respects_max_concurrency(make_run, task: TaskInput) -> None:
    plan = make_plan(*(subtask(f"s{i}") for i in range(1, 6)))
    probe = ConcurrencyProbe()
    reviews = {f"s{i}": [approve()] for i in range(1, 6)}
    ctx, _ = make_context(models(plan, reviews), probe, max_concurrency=2)  # type: ignore[arg-type]
    run = make_run(ctx)
    await run.start(task)

    assert probe.peak == 2  # parallel, but never more than the limit
    assert set((await run.state())["subtask_status"].values()) == {"approved"}


@pytest.mark.parametrize("order", [("s1", "s2"), ("s2", "s1")])
async def test_fr42_two_parallel_interrupts_resume_by_id_in_either_order(
    make_run, task: TaskInput, order: tuple[str, str]
) -> None:
    plan = make_plan(subtask("s1"), subtask("s2"))
    specialists = FakeSpecialists(outcomes={"s1": [fail(), fail()], "s2": [fail(), fail()]})
    run = make_run(make_context(models(plan, {}), specialists)[0])
    await run.start(task)

    pending = {i.value["subtask"]: i.id for i in await run.interrupts()}
    assert set(pending) == {"s1", "s2"}
    first, second = order
    await run.resume(decision("take_over", output=f"human {first}"), interrupt_id=pending[first])
    [still] = await run.interrupts()
    assert still.id == pending[second]  # the other escalation stays open
    await run.resume(decision("take_over", output=f"human {second}"), interrupt_id=pending[second])

    state = await run.state()
    assert state["subtask_status"] == {"s1": "taken_over", "s2": "taken_over"}
    # Resuming re-entered only the escalate step: no specialist ran again (SPEC 11.6).
    assert len(specialists.requests) == 4
    assert state["subtask_results"]["s1"].output == "human s1"
    assert state["subtask_results"]["s2"].output == "human s2"
    assert run.event_types()[-1] == "run.completed"
