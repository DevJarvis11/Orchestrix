"""BudgetMiddleware (SPEC 15.3, 14.1; FR-63, NFR-07): checked before every specialist model call.

`before_model` estimates the next call's input tokens and compares the run's usage plus the
estimate with every cap. At 80% it emits `budget.warning` once (level `notify`: never pauses);
at 100% it raises the `budget_100` escalation: a human raises the caps or stops the run.
A raised budget lives in the agent's own checkpointed state, so a resumed agent keeps it.
"""

import contextlib
from typing import Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from orchestrix_agents.context import RunContext, SpecialistRequest
from orchestrix_agents.llm import usage_of
from orchestrix_agents.nodes._shared import ask_human
from orchestrix_core.budgets import check_budget
from orchestrix_core.schemas import (
    Budget,
    EscalationPayload,
    EscalationRecord,
    ModelPrice,
    Usage,
)
from orchestrix_core.schemas.events import BudgetEvent
from orchestrix_core.telemetry import span

CHARS_PER_TOKEN = 3.5  # conservative: over-estimating stops a call early, never late


class BudgetExhausted(Exception):
    """A human chose to stop the run at budget_100."""


def estimate_tokens(messages: list[BaseMessage], system_prompt: str | None) -> int:
    """Input-token estimate before a call (deviation: no gateway round-trip per call)."""
    chars = len(system_prompt or "")
    for m in messages:
        chars += len(m.content) if isinstance(m.content, str) else len(str(m.content))
        chars += sum(len(str(tc.get("args", ""))) for tc in getattr(m, "tool_calls", []) or [])
    return int(chars / CHARS_PER_TOKEN) + 16 * len(messages)


class BudgetState(AgentState[Any]):
    orchestrix_budget: NotRequired[dict[str, Any]]  # a human-raised budget; read by the runner


class BudgetMiddleware(AgentMiddleware[BudgetState, Any, Any]):
    state_schema = BudgetState

    def __init__(
        self,
        ctx: RunContext,
        request: SpecialistRequest,
        price: ModelPrice | None,
        system_prompt: str,
        escalations: list[EscalationRecord],
        tool_calls: "ToolCounter",
    ) -> None:
        super().__init__()
        self._ctx = ctx
        self._request = request
        self._price = price
        self._system = system_prompt
        self._escalations = escalations
        self._tool_calls = tool_calls
        self._warned = False
        self.raised: Budget | None = None  # read back by the specialist runner

    def _budget(self, state: BudgetState) -> Budget:
        stored = state.get("orchestrix_budget")
        if stored:
            return Budget.model_validate(stored)
        return self._request.budget or self._ctx.budget

    def _used(self, state: BudgetState) -> Usage:
        used = self._request.usage_so_far + Usage(tool_calls=self._tool_calls.count)
        for m in state.get("messages", []):
            if isinstance(m, AIMessage):
                used = used + usage_of(m, self._price)
        return used

    async def abefore_model(
        self, state: BudgetState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        budget = self._budget(state)
        estimate = estimate_tokens(list(state.get("messages", [])), self._system)
        projected = self._used(state) + Usage(
            input_tokens=estimate,
            usd=(estimate * self._price.input_per_mtok / 1_000_000) if self._price else 0.0,
        )
        status = check_budget(projected, budget, active_seconds=self._ctx.active_seconds())
        if status.level == "warning" and not self._warned:
            self._warned = True
            with contextlib.suppress(RuntimeError):
                get_stream_writer()(
                    BudgetEvent(
                        type="budget.warning",
                        run_id=self._ctx.run_id,
                        metric=status.metric or "",
                        used=status.used,
                        cap=status.cap,
                    ).model_dump(mode="json")
                )
        if status.level != "exceeded":
            return None
        with span(
            "budget.exceeded",
            **{"orchestrix.metric": status.metric, "orchestrix.subtask": self._request.subtask.key},
        ):
            pass

        subtask = self._request.subtask
        decision = ask_human(
            EscalationPayload(
                trigger="budget_100",
                level="approve_action",
                title=f"Budget cap reached on {status.metric} in subtask {subtask.key}: "
                "raise it or stop",
                task_summary=subtask.description,
                plan_progress=[
                    {"key": subtask.key, "specialist": subtask.specialist, "status": "running"}
                ],
                proposed={"budget": budget.model_dump(), "metric": status.metric},
                reasoning=f"{status.metric}: {status.used:g} used (with the next call's "
                f"estimate) of {status.cap:g}",
                related_memories=[],
                allowed_actions=["approve_with_edits", "reject"],
                subtask=subtask.key,
            )
        )
        self._escalations.append(
            EscalationRecord(
                trigger="budget_100",
                level="approve_action",
                subtask=subtask.key,
                resolution=decision.action,
            )
        )
        if decision.action == "reject":
            raise BudgetExhausted(f"stopped by {decision.actor_id} at the {status.metric} cap")
        raised = Budget.model_validate({**budget.model_dump(), **(decision.edits or {})})
        self.raised = raised
        return {"orchestrix_budget": raised.model_dump()}


class ToolCounter:
    """Shared with ToolAccounting through CallLog; a tiny view so budget sees tool calls."""

    def __init__(self, source: Any) -> None:
        self._source = source

    @property
    def count(self) -> int:
        return int(getattr(self._source, "tool_calls", 0))
