"""Escalations API (SPEC 14.3, 17.1; FR-43, FR-44): inbox, detail, decision, reviewer chat."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from orchestrix_api import chat
from orchestrix_api.deps import CurrentUser, Resources, User, has_role
from orchestrix_core import metrics
from orchestrix_core.db.repos.escalations import AlreadyResolved, EscalationView
from orchestrix_core.queue import RESUME_RUN, enqueue
from orchestrix_core.schemas import EscalationDecision, parse_decision
from orchestrix_core.schemas.events import EscalationEvent
from orchestrix_core.telemetry import span
from orchestrix_policy.client import Actor, EscalationPolicyInput

router = APIRouter(prefix="/v1/escalations", tags=["escalations"])


class EscalationSummary(BaseModel):
    id: UUID
    run_id: UUID
    task_id: UUID
    task_title: str
    trigger: str
    level: str
    title: str
    subtask: str | None
    status: str
    due_at: datetime
    created_at: datetime


class ActionView(BaseModel):
    actor_id: UUID
    action: str
    body: dict[str, Any]
    created_at: datetime


class MessageView(BaseModel):
    role: str
    author_id: UUID | None
    body: str
    created_at: datetime


class EscalationDetail(EscalationSummary):
    payload: dict[str, Any]
    requester_id: UUID
    actions: list[ActionView]
    messages: list[MessageView]
    can_decide: bool


class DecisionBody(BaseModel):
    """EscalationDecision (SPEC 14.2) minus actor_id, which comes from the token."""

    action: Literal["approve", "approve_with_edits", "reject", "take_over", "answer"]
    edits: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=4000)
    on_reject: Literal["retry", "skip", "fail_task"] | None = None
    output: str | None = Field(default=None, max_length=200_000)
    answers: list[str] | None = Field(default=None, max_length=3)


class DecisionAccepted(BaseModel):
    escalation_id: UUID
    run_id: UUID
    status: str


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def _summary(v: EscalationView) -> EscalationSummary:
    e = v.escalation
    return EscalationSummary(
        id=e.id,
        run_id=e.run_id,
        task_id=v.task_id,
        task_title=v.task_title,
        trigger=e.trigger,
        level=e.level,
        title=str(e.payload.get("title", e.trigger)),
        subtask=e.payload.get("subtask"),
        status=e.status,
        due_at=e.due_at,
        created_at=e.created_at,
    )


async def _may_decide(res: Resources, user: CurrentUser, view: EscalationView) -> bool:
    team_ids = await res.users.team_ids(user.id)
    return await res.policy.may_decide(
        EscalationPolicyInput(
            actor=Actor(
                id=str(user.id), roles=sorted(user.roles), team_ids=[str(t) for t in team_ids]
            ),
            escalation={
                "level": view.escalation.level,
                "assignee_team_id": str(view.escalation.assignee_team_id or view.team_id),
            },
            task={"requester_id": str(view.requester_id)},
        )
    )


async def _visible(res: Resources, user: CurrentUser, escalation_id: UUID) -> EscalationView:
    view = await res.escalations.get(escalation_id)
    if view is not None:
        if view.requester_id == user.id or has_role(user, "operator"):
            return view
        team = view.escalation.assignee_team_id or view.team_id  # same rule as inbox and OPA
        if has_role(user, "reviewer") and await res.users.is_team_member(user.id, team):
            return view
    raise HTTPException(status.HTTP_404_NOT_FOUND, "escalation not found")


@router.get("")
async def inbox(
    res: Resources,
    user: User,
    status_filter: Annotated[str | None, Query(alias="status")] = "open",
    level: str | None = None,
) -> list[EscalationSummary]:
    views = await res.escalations.inbox(
        user_id=user.id,
        team_ids=await res.users.team_ids(user.id),
        reviewer=has_role(user, "reviewer"),
        status=status_filter or None,
        level=level,
    )
    return [_summary(v) for v in views]


@router.get("/{escalation_id}")
async def detail(escalation_id: UUID, res: Resources, user: User) -> EscalationDetail:
    view = await _visible(res, user, escalation_id)
    actions = await res.escalations.actions(escalation_id)
    messages = await res.escalations.messages(escalation_id)
    return EscalationDetail(
        **_summary(view).model_dump(),
        payload=view.escalation.payload,
        requester_id=view.requester_id,
        actions=[
            ActionView(actor_id=a.actor_id, action=a.action, body=a.body, created_at=a.created_at)
            for a in actions
        ],
        messages=[
            MessageView(role=m.role, author_id=m.author_id, body=m.body, created_at=m.created_at)
            for m in messages
        ],
        can_decide=view.escalation.status == "open" and await _may_decide(res, user, view),
    )


@router.post("/{escalation_id}/decision", status_code=status.HTTP_202_ACCEPTED)
async def decide(
    escalation_id: UUID, body: DecisionBody, res: Resources, user: User
) -> DecisionAccepted:
    """SPEC 14.3 step 4: OPA permission (four-eyes), allowed_actions, action + audit, resume."""
    view = await _visible(res, user, escalation_id)
    if view.escalation.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"escalation is {view.escalation.status}")
    if not await _may_decide(res, user, view):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "not allowed to decide this escalation (role, team, or your own task)",
        )
    allowed = list(view.escalation.payload.get("allowed_actions", []))
    try:
        decision = parse_decision(
            {**body.model_dump(), "actor_id": str(user.id)},
            allowed,  # type: ignore[arg-type]
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    stored = decision.model_dump(mode="json")
    try:
        esc, session = await res.escalations.decide(escalation_id, user.id, decision.action, stored)
    except AlreadyResolved as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "escalation already resolved") from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "escalation not found") from exc
    try:
        await res.audit.write(
            actor_id=user.id,
            action=f"escalation.{decision.action}",
            target_type="escalation",
            target_id=str(escalation_id),
            before={"status": "open", "trigger": esc.trigger, "level": esc.level},
            after=_audit_body(decision),
            reason=decision.reason,
            session=session,
        )
        await session.commit()
    finally:
        await session.close()
    await res.events.emit(
        esc.run_id,
        EscalationEvent(
            type="escalation.resolved",
            run_id=str(esc.run_id),
            interrupt_id=esc.interrupt_id,
            trigger=esc.trigger,
            level=esc.level,  # type: ignore[arg-type]
            subtask=esc.payload.get("subtask"),
            actor=user.email or str(user.id),
            action=decision.action,
        ).model_dump(mode="json"),
    )
    metrics.ESCALATION_WAIT.labels(esc.level, esc.trigger).observe(
        (datetime.now(UTC) - esc.created_at).total_seconds()
    )
    with span(
        "escalation.resolve",
        **{"orchestrix.trigger": esc.trigger, "orchestrix.action": decision.action},
    ):
        await enqueue(RESUME_RUN, str(esc.run_id))
    return DecisionAccepted(escalation_id=escalation_id, run_id=esc.run_id, status="resolved")


def _audit_body(decision: EscalationDecision) -> dict[str, Any]:
    """What changed, without copying a whole take-over document into the log."""
    out: dict[str, Any] = {"action": decision.action, "on_reject": decision.on_reject}
    if decision.edits is not None:
        out["edits"] = decision.edits
    if decision.output is not None:
        out["output_chars"] = len(decision.output)
    if decision.answers is not None:
        out["answers"] = len(decision.answers)
    return out


@router.post("/{escalation_id}/chat")
async def chat_about(
    escalation_id: UUID, body: ChatBody, res: Resources, user: User
) -> list[MessageView]:
    """FR-44: questions answered from a read-only snapshot of run state; never changes the run."""
    view = await _visible(res, user, escalation_id)
    if not await _may_decide(res, user, view):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the deciders may chat about it")
    run_id = view.escalation.run_id
    plan = await res.runs.latest_plan(run_id)
    subtasks = await res.runs.subtasks(run_id, plan.version) if plan else []
    snapshot = json.dumps(
        {
            "escalation": view.escalation.payload,
            "plan": plan.body if plan else None,
            "subtasks": [
                {
                    "key": s.key,
                    "specialist": s.specialist,
                    "status": s.status,
                    "attempts": s.attempts,
                    "revisions": s.revisions,
                    "output": str((s.result or {}).get("output", ""))[:3000],
                    "sources": (s.result or {}).get("sources", []),
                    "notes": (s.result or {}).get("notes", ""),
                }
                for s in subtasks
            ],
        },
        default=str,
    )[:60_000]
    previous = await res.escalations.messages(escalation_id)
    history = [
        {"role": "user" if m.role == "reviewer" else "assistant", "content": m.body}
        for m in previous[-10:]
    ] + [{"role": "user", "content": body.message}]
    await res.escalations.add_message(escalation_id, user.id, "reviewer", body.message)
    try:
        reply = await chat.answer(res, run_id, snapshot, history)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "model gateway unavailable"
        ) from exc
    await res.escalations.add_message(escalation_id, None, "agent", reply or "(no answer)")
    return [
        MessageView(role=m.role, author_id=m.author_id, body=m.body, created_at=m.created_at)
        for m in await res.escalations.messages(escalation_id)
    ]
