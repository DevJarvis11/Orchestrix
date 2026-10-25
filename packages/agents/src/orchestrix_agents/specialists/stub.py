"""M2 stub specialist: one structured call, no tools. Replaced by create_agent specialists in M3."""

from langchain_core.messages import HumanMessage

from orchestrix_agents.context import (
    RunContext,
    SpecialistError,
    SpecialistOutcome,
    SpecialistRequest,
)
from orchestrix_agents.llm import StructuredOutputError, invoke_structured
from orchestrix_agents.prompting import render_prompt
from orchestrix_core.schemas import SubtaskResult


class StubSpecialistRunner:
    async def run(self, request: SpecialistRequest, ctx: RunContext) -> SpecialistOutcome:
        llm = ctx.services.llm
        prompt = render_prompt(
            "specialist_stub.j2",
            subtask=request.subtask,
            inputs=request.inputs,
            feedback=request.feedback,
            hint=request.hint,
        )
        try:
            result = await invoke_structured(
                llm.chat("specialist"),
                SubtaskResult,
                [HumanMessage(prompt)],
                price=llm.price("specialist"),
            )
        except StructuredOutputError as exc:
            raise SpecialistError(str(exc), retryable=False) from exc
        return SpecialistOutcome(result=result.value, usage=result.usage)
