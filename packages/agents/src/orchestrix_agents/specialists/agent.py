"""create_agent specialists (SPEC 12.2): bounded tool loop, typed SubtaskResult, Orchestrix middleware.

Middleware, outermost first:
    Budget (before each model call)
    ToolCallLimit -> InjectionGuard -> ToolAccounting -> Policy -> ToolRetry -> MCP tool
Tracing is the LlmCallRecorder callback on the chat model (llm_calls rows, llm.completed).
"""

# create_agent returns a langgraph CompiledStateGraph; langgraph is a namespace package, so
# pyright ignores its py.typed (same relaxation as graph.py).
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp

from orchestrix_agents.context import (
    RunContext,
    SpecialistError,
    SpecialistOutcome,
    SpecialistRequest,
)
from orchestrix_agents.llm import StructuredOutputError, invoke_structured, usage_of
from orchestrix_agents.middleware.budget import BudgetExhausted, BudgetMiddleware, ToolCounter
from orchestrix_agents.middleware.policy import PolicyMiddleware
from orchestrix_agents.middleware.tools import (
    CallLog,
    InjectionGuardMiddleware,
    ToolAccountingMiddleware,
    ToolRetryMiddleware,
)
from orchestrix_agents.prompting import render_prompt
from orchestrix_core.schemas import Budget, CallTag, Purpose, SubtaskResult, Usage
from orchestrix_tools.client import CURRENT_RUN, ToolBox

EnabledTools = Callable[[], Awaitable[set[str] | None]]


def _node_config() -> RunnableConfig | None:
    """The calling node's config (so the agent is a checkpointed subgraph), if inside a graph."""
    try:
        return get_config()
    except RuntimeError:
        return None


def purpose_of(request: SpecialistRequest) -> Purpose:
    if request.feedback:
        return "revision"
    if request.hint or request.attempt:
        return "retry"
    return "execute"


class AgentSpecialistRunner:
    def __init__(self, toolbox: ToolBox, enabled_tools: EnabledTools) -> None:
        self._toolbox = toolbox
        self._enabled = enabled_tools

    async def run(self, request: SpecialistRequest, ctx: RunContext) -> SpecialistOutcome:
        subtask = request.subtask
        llm = ctx.services.llm
        tools = self._toolbox.for_specialist(subtask.specialist, await self._enabled())
        log = CallLog(tainted=request.tainted_inputs)
        system_prompt = render_prompt(
            f"specialists/{subtask.specialist}.j2", tools=[t.name for t in tools]
        )
        budget = BudgetMiddleware(
            ctx,
            request,
            llm.price("specialist"),
            system_prompt,
            log.escalations,
            ToolCounter(log),
        )
        middleware: list[AgentMiddleware[Any, Any, Any]] = [
            budget,
            # FR-21: block calls past the limit and let the model answer with what it has.
            ToolCallLimitMiddleware(run_limit=subtask.max_tool_calls, exit_behavior="continue"),
            InjectionGuardMiddleware(log),
        ]
        if ctx.services.tool_calls is not None:
            middleware.append(
                ToolAccountingMiddleware(ctx.services.tool_calls, ctx.run_id, subtask.key, log)
            )
        middleware += [PolicyMiddleware(ctx, subtask, log), ToolRetryMiddleware()]
        tag = CallTag(
            agent=subtask.specialist,
            purpose=purpose_of(request),
            subtask_key=subtask.key,
            attempt=request.attempt,
            revision=request.revision,
        )
        agent = create_agent(
            model=llm.chat("specialist", tag),
            tools=tools,
            system_prompt=system_prompt,
            response_format=SubtaskResult,
            middleware=middleware,
            name=f"specialist_{subtask.key}",
        )
        message = render_prompt(
            "specialist_task.j2",
            subtask=subtask,
            inputs=request.inputs,
            feedback=request.feedback,
            hint=request.hint,
            memories=request.memories,
        )
        seconds = ctx.specialist_seconds.get(subtask.specialist, 300)
        token = CURRENT_RUN.set(ctx.run_id)
        try:
            # The node's config makes the agent a checkpointed subgraph: an interrupt inside it
            # pauses the run, and a resume skips the tool calls it already made (SPEC 12.2).
            async with asyncio.timeout(seconds):  # SPEC 12.1 limit; a pause stops the clock
                state = cast(
                    dict[str, Any],
                    await agent.ainvoke({"messages": [HumanMessage(message)]}, _node_config()),
                )
        except GraphBubbleUp:
            raise  # interrupts and other graph control flow must reach LangGraph
        except BudgetExhausted as exc:
            raise SpecialistError(str(exc), retryable=False, stop="budget_exceeded") from exc
        except TimeoutError as exc:
            raise SpecialistError(
                f"{subtask.specialist} exceeded its {seconds}s time limit", retryable=False
            ) from exc
        except httpx.HTTPError as exc:
            raise SpecialistError(f"model or tool transport failed: {exc}", retryable=True) from exc
        except Exception as exc:
            raise SpecialistError(f"{type(exc).__name__}: {exc}", retryable=False) from exc
        finally:
            CURRENT_RUN.reset(token)

        usage = Usage(tool_calls=log.tool_calls)
        price = llm.price("specialist")
        messages: list[Any] = list(state.get("messages", []))
        for msg in messages:
            if isinstance(msg, AIMessage):
                usage = usage + usage_of(msg, price)
        structured = state.get("structured_response")
        if not isinstance(structured, SubtaskResult):
            # Model-agnostic: some models end the loop in prose instead of calling the
            # structured-output tool. Ask once more for the typed result (json_schema, then
            # function calling, then one repair) from a compact transcript without tool calls.
            final = next(
                (m for m in reversed(messages) if isinstance(m, AIMessage) and m.content), None
            )
            try:
                typed = await invoke_structured(
                    llm.chat("specialist", tag),
                    SubtaskResult,
                    [
                        SystemMessage(system_prompt),
                        HumanMessage(message),
                        AIMessage(final.content if final else "(no answer)"),
                        HumanMessage(
                            render_prompt("specialist_finalize.j2", subtask_key=subtask.key)
                        ),
                    ],
                    price=price,
                )
            except StructuredOutputError as exc:
                raise SpecialistError(
                    "specialist finished without a structured result", retryable=False
                ) from exc
            structured, usage = typed.value, usage + typed.usage
        result = structured.model_copy(
            update={"key": subtask.key, "tool_call_ids": [*log.tool_call_ids]}
        )
        if log.warnings:
            security_note = "Security review required: " + "; ".join(log.warnings)
            result = result.model_copy(
                update={"notes": "\n\n".join(filter(None, (result.notes, security_note)))}
            )
        raised = state.get("orchestrix_budget")
        return SpecialistOutcome(
            result=result,
            usage=usage,
            tainted=log.tainted,
            warnings=log.warnings,
            budget=Budget.model_validate(raised) if raised else budget.raised,
            escalations=list(log.escalations),
        )
