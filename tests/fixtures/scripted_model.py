"""ScriptedChatModel: a fake chat model returning pre-written replies (SPEC 20.1).

A reply is an AIMessage (tool calls included), a Pydantic model or dict (structured output),
a str (plain content, or raw JSON for structured output), or an Exception to raise.
Replies come from a queue, or from `responder(messages)` when parallel branches would make
queue order nondeterministic.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field, SkipValidation, ValidationError

Reply = AIMessage | BaseModel | dict[str, Any] | str | Exception
USAGE = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}


class ScriptedChatModel(BaseChatModel):
    script: SkipValidation[list[Reply]] = Field(default_factory=list)  # keep replies as given
    responder: Callable[[list[BaseMessage]], Reply] | None = None
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self, messages: list[BaseMessage]) -> Reply:
        self.calls.append(list(messages))
        if self.responder is not None:
            return self.responder(messages)
        if not self.script:
            raise AssertionError(
                f"ScriptedChatModel ran out of replies after {len(self.calls)} calls"
            )
        return self.script.pop(0)

    @staticmethod
    def _as_message(reply: Reply) -> AIMessage:
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, AIMessage):
            msg = reply
        elif isinstance(reply, BaseModel):
            msg = AIMessage(content=reply.model_dump_json())
        elif isinstance(reply, dict):
            msg = AIMessage(content=json.dumps(reply))
        else:
            msg = AIMessage(content=reply)
        if msg.usage_metadata is None:
            msg = msg.model_copy(update={"usage_metadata": USAGE})
        return msg

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=self._as_message(self._next(messages)))]
        )

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, AIMessage]:
        return self  # tool calls come from the script

    def with_structured_output(  # type: ignore[override]
        self, schema: Any, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[Any, Any]:
        def respond(messages: Any) -> Any:
            msgs = messages.to_messages() if hasattr(messages, "to_messages") else list(messages)
            reply = self._next(msgs)
            raw = self._as_message(reply)
            parsed: Any = None
            error: Exception | None = None
            try:
                if isinstance(reply, BaseModel | dict):
                    parsed = schema.model_validate(
                        reply if isinstance(reply, dict) else reply.model_dump()
                    )
                else:
                    parsed = schema.model_validate_json(str(raw.content))
            except ValidationError as exc:
                error = exc
            if include_raw:
                return {"raw": raw, "parsed": parsed, "parsing_error": error}
            if error is not None:
                raise error
            return parsed

        return RunnableLambda(respond)
