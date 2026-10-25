"""Interrupt payload and resume value (SPEC 14.2)."""

from typing import Any, Literal

from pydantic import BaseModel, model_validator

DecisionAction = Literal["approve", "approve_with_edits", "reject", "take_over", "answer"]


class EscalationPayload(BaseModel):
    """What interrupt() receives. JSON-serialisable; mirrored into the escalations table."""

    trigger: str
    level: Literal["clarify", "approve_action", "approve_plan", "take_over"]
    title: str
    task_summary: str
    plan_progress: list[dict[str, Any]]  # key, specialist, status
    proposed: dict[str, Any]  # tool + args, or plan, or draft output
    reasoning: str
    related_memories: list[dict[str, Any]]  # id, text
    questions: list[str] = []  # clarify only
    allowed_actions: list[DecisionAction]
    subtask: str | None = None


class EscalationDecision(BaseModel):
    """What Command(resume={interrupt_id: decision}) carries."""

    action: DecisionAction
    edits: dict[str, Any] | None = None  # edited tool args or edited plan (re-validated)
    reason: str | None = None  # required for reject
    on_reject: Literal["retry", "skip", "fail_task"] | None = None
    output: str | None = None  # take_over
    answers: list[str] | None = None  # clarify
    actor_id: str

    @model_validator(mode="after")
    def _check(self) -> "EscalationDecision":
        if self.action == "reject" and not self.reason:
            raise ValueError("reject requires a reason")
        if self.action == "take_over" and not self.output:
            raise ValueError("take_over requires output")
        if self.action == "approve_with_edits" and not self.edits:
            raise ValueError("approve_with_edits requires edits")
        if self.action == "answer" and not self.answers:
            raise ValueError("answer requires answers")
        return self


def parse_decision(value: object, allowed: list[DecisionAction]) -> EscalationDecision:
    """Validate a resume value against the payload's allowed actions (SPEC 14.2)."""
    decision = EscalationDecision.model_validate(value)
    if decision.action not in allowed:
        raise ValueError(f"action {decision.action!r} not allowed here (allowed: {allowed})")
    return decision
