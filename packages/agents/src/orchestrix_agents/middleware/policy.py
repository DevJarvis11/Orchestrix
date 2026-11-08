"""PolicyMiddleware (SPEC 12.2, 14.1, 15.1; FR-22, FR-40, FR-90).

Every tool call asks OPA first: `deny` returns a refusal to the model, `require_approval`
pauses the run with interrupt() BEFORE the tool executes (SPEC 11.6), so an approval gate can
never follow its own side effect. Edited arguments go back through the policy check (14.2).
Replays run with simulated side effects: sensitive tools answer without executing (19.4).
"""

import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from orchestrix_agents.context import RunContext
from orchestrix_agents.middleware.tools import CallLog
from orchestrix_agents.nodes._shared import ask_human
from orchestrix_core.schemas import (
    EscalationPayload,
    EscalationRecord,
    PlannedSubtask,
    PolicyDecision,
)
from orchestrix_core.schemas.events import ToolEvent
from orchestrix_tools.catalogue import CATALOGUE, canonical

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]


def refusal(request: ToolCallRequest, message: str, decision: PolicyDecision) -> ToolMessage:
    """A failed tool result the model can read; the artifact tells accounting it was denied."""
    return ToolMessage(
        content=json.dumps({"error_code": "policy_denied", "message": message, "retryable": False}),
        tool_call_id=str(request.tool_call.get("id", "")),
        name=request.tool_call["name"],
        status="error",
        artifact={"orchestrix_status": "denied", "policy_decision": decision.model_dump()},
    )


def _reasoning(request: ToolCallRequest) -> str:
    """The model's own words before the call: what a reviewer sees as the agent's reasoning."""
    raw: Any = request.state
    state: dict[str, Any] = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    messages: list[Any] = list(state.get("messages", []))
    for message in reversed(messages):
        if (
            isinstance(message, AIMessage)
            and isinstance(message.content, str)
            and message.content.strip()
        ):
            return message.content.strip()[:2000]
    return "The agent proposed this call without further explanation."


class PolicyMiddleware(AgentMiddleware):  # pyright: ignore[reportMissingTypeArgument]
    def __init__(self, ctx: RunContext, subtask: PlannedSubtask, log: CallLog) -> None:
        super().__init__()
        self._ctx = ctx
        self._subtask = subtask
        self._log = log

    async def _check(self, name: str, args: dict[str, Any]) -> PolicyDecision:
        policy = self._ctx.services.policy
        if policy is None:
            return PolicyDecision(result="allow")
        return await policy.check_tool(
            run_id=self._ctx.run_id,
            tool=name,
            specialist=self._subtask.specialist,
            args=args,
            user_id=self._ctx.user_id,
            team_id=self._ctx.team_id,
            tainted=self._log.tainted,
        )

    async def _deny(
        self, request: ToolCallRequest, name: str, args: dict[str, Any], decision: PolicyDecision
    ) -> ToolMessage:
        policy = self._ctx.services.policy
        if policy is not None:
            await policy.record_denial(
                run_id=self._ctx.run_id, tool=name, args=args, reasons=decision.reasons
            )
        with contextlib.suppress(RuntimeError):
            get_stream_writer()(
                ToolEvent(
                    type="tool.denied",
                    run_id=self._ctx.run_id,
                    tool=name,
                    subtask=self._subtask.key,
                    reason="; ".join(decision.reasons),
                ).model_dump(mode="json")
            )
        return refusal(request, "denied by policy: " + "; ".join(decision.reasons), decision)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command[Any]:
        name = canonical(request.tool_call["name"])
        args: dict[str, Any] = dict(request.tool_call.get("args", {}))
        spec = CATALOGUE.get(name)

        if self._ctx.side_effects == "simulated" and spec is not None and spec.side_effecting:
            return ToolMessage(
                content=json.dumps({"status": "simulated", "note": "replay: not executed"}),
                tool_call_id=str(request.tool_call.get("id", "")),
                name=request.tool_call["name"],
                artifact={"orchestrix_status": "ok", "policy_decision": {"result": "simulated"}},
            )

        decision = await self._check(name, args)
        if decision.result == "deny":
            return await self._deny(request, name, args, decision)

        if decision.result == "require_approval":
            sensitive = spec is not None and spec.sensitive
            trigger = "sensitive_tool" if sensitive else "policy_require_approval"
            level = self._ctx.escalation_levels.get(trigger, "approve_action")
            human = ask_human(
                EscalationPayload(
                    trigger=trigger,
                    level=level,  # type: ignore[arg-type]
                    title=f"Approve {name} for subtask {self._subtask.key}",
                    task_summary=self._subtask.description,
                    plan_progress=[
                        {
                            "key": self._subtask.key,
                            "specialist": self._subtask.specialist,
                            "status": "running",
                        }
                    ],
                    proposed={"tool": name, "args": args, "reasons": decision.reasons},
                    reasoning=_reasoning(request),
                    related_memories=[],
                    allowed_actions=["approve", "approve_with_edits", "reject"],
                    subtask=self._subtask.key,
                )
            )
            self._log.escalations.append(
                EscalationRecord(
                    trigger=trigger,
                    level=level,  # type: ignore[arg-type]
                    subtask=self._subtask.key,
                    resolution=human.action,
                )
            )
            if human.action == "reject":
                return refusal(
                    request,
                    f"a human reviewer rejected this call: {human.reason}. "
                    "Try an alternative, or explain the gap in your notes.",
                    decision,
                )
            if human.action == "approve_with_edits" and human.edits:
                args = {**args, **human.edits}
                # Edited arguments are re-checked; only a deny can stop them now (14.2).
                recheck = await self._check(name, args)
                if recheck.result == "deny":
                    return await self._deny(request, name, args, recheck)
                request = request.override(tool_call={**request.tool_call, "args": args})
            decision = PolicyDecision(
                result="allow", reasons=[f"approved by {human.actor_id}", *decision.reasons]
            )

        result = await handler(request)
        if isinstance(result, ToolMessage):
            raw: Any = result.artifact
            artifact: dict[str, Any] = (
                dict(cast(dict[str, Any], raw)) if isinstance(raw, dict) else {"structured": raw}
            )
            artifact["policy_decision"] = decision.model_dump()
            artifact["executed_args"] = args  # after any human edit (accounting records these)
            return result.model_copy(update={"artifact": artifact})
        return result
