"""clarify: up to 3 questions to the requester before planning (FR-15; SPEC 14.1 ambiguous_request,
level clarify). The plan node asked the model; this node only pauses, so a resume replays the
requester's answers without asking the model again (SPEC 11.6)."""

from typing import Any

from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes._shared import STEP, ask_human
from orchestrix_agents.state import RunState
from orchestrix_core.schemas import EscalationPayload, EscalationRecord


async def clarify(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    task = state["task"]
    questions = list(state.get("clarify_questions", []))[:3]
    decision = ask_human(
        EscalationPayload(
            trigger="ambiguous_request",
            level="clarify",
            title=f"The supervisor has {len(questions)} question(s) before planning",
            task_summary=f"{task.title}: {task.description[:500]}",
            plan_progress=[],
            proposed={"questions": questions},
            reasoning="The request is ambiguous; answers change the plan.",
            related_memories=[{"id": m.id, "text": m.text} for m in state.get("memories", [])],
            questions=questions,
            allowed_actions=["answer"],
        )
    )
    # The worker set awaiting_human while paused; the run is planning again (FR-01).
    await runtime.context.services.recorder.set_task_status(runtime.context.task_id, "planning")
    answers = list(decision.answers or [])
    qa = [f"Q: {q}\nA: {a}" for q, a in zip(questions, answers, strict=False)]
    return {
        "clarifications": qa,  # the requester's own words (valid preference sources, 14.4)
        "clarify_questions": [],
        "escalations": [
            EscalationRecord(trigger="ambiguous_request", level="clarify", resolution="answer")
        ],
        "usage": STEP,
    }
