"""Role resolution (amendment A2) and structured output policy (SPEC 16.3)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from orchestrix_agents.llm import (
    StructuredOutputError,
    invoke_structured,
    resolve_models,
    served_model,
    strip_think,
    usage_of,
)
from orchestrix_core.schemas import ModelPrice, Review
from tests.fixtures.scripted_model import ScriptedChatModel

SETTINGS = {
    "planner": "gpt-oss-120b",
    "specialist": "gpt-oss-120b",
    "reviewer": "gpt-oss-120b",
    "fast": "gpt-oss-120b",
}


def test_a2_resolution_order_override_then_admin_then_settings() -> None:
    resolved = resolve_models({"planner": "qwen3-8b-local"}, {"reviewer": "cerebro-llm"}, SETTINGS)
    assert resolved == {
        "planner": "qwen3-8b-local",
        "specialist": "gpt-oss-120b",
        "reviewer": "cerebro-llm",
        "fast": "gpt-oss-120b",
    }


def test_a2_missing_role_is_an_error() -> None:
    with pytest.raises(ValueError, match="fast"):
        resolve_models({}, {}, {k: v for k, v in SETTINGS.items() if k != "fast"})


def test_strip_think_removes_reasoning_blocks() -> None:
    assert strip_think('<think>hmm\nmaybe</think>\n{"a": 1}') == '{"a": 1}'


def test_usage_and_cost_from_message() -> None:
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
    )
    usage = usage_of(msg, ModelPrice(input_per_mtok=0.15, output_per_mtok=0.60))
    assert (usage.input_tokens, usage.output_tokens, usage.usd) == (1000, 500, 0.00045)


def test_a2_served_model_prefers_litellm_group_header() -> None:
    msg = AIMessage(
        content="x",
        response_metadata={
            "model_name": "gpt-oss-120b",
            "headers": {"x-litellm-model-group": "qwen3-8b-local"},
        },
    )
    assert served_model(msg) == "qwen3-8b-local"  # the fallback answered


REVIEW = Review(verdict="approve", score=8, feedback="ok")


class JsonSchemaParserFailureModel(ScriptedChatModel):
    """Models that accept response_format but emit prose cause LangChain's parser to raise."""

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):  # type: ignore[override, no-untyped-def]
        if kwargs["method"] == "json_schema":

            def reject(_messages):  # type: ignore[no-untyped-def]
                return schema.model_validate({"verdict": "not-a-valid-review"})

            return RunnableLambda(reject)
        return super().with_structured_output(schema, include_raw=include_raw, **kwargs)


async def test_structured_output_parses_first_reply() -> None:
    result = await invoke_structured(
        ScriptedChatModel(script=[REVIEW]), Review, [HumanMessage("x")]
    )
    assert result.value == REVIEW
    assert result.usage.tokens == 150


async def test_structured_output_strips_think_blocks() -> None:
    raw = f"<think>pondering</think>{REVIEW.model_dump_json()}"
    result = await invoke_structured(ScriptedChatModel(script=[raw]), Review, [HumanMessage("x")])
    assert result.value == REVIEW


async def test_structured_output_repairs_once_with_the_error() -> None:
    model = ScriptedChatModel(script=['{"verdict": "maybe"}', REVIEW])
    result = await invoke_structured(model, Review, [HumanMessage("x")])
    assert result.value == REVIEW
    assert "did not match the required JSON schema" in str(model.calls[1][-1].content)
    assert result.usage.tokens == 300  # both calls are paid for


async def test_structured_output_uses_function_calling_after_json_schema_parser_failure() -> None:
    result = await invoke_structured(
        JsonSchemaParserFailureModel(script=[REVIEW]), Review, [HumanMessage("x")]
    )
    assert result.value == REVIEW


async def test_structured_output_raises_after_second_failure() -> None:
    with pytest.raises(StructuredOutputError, match="Review"):
        await invoke_structured(
            ScriptedChatModel(script=["nope", "still nope"]), Review, [HumanMessage("x")]
        )
