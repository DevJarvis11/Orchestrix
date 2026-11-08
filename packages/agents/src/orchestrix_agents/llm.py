"""Role -> gateway model, and structured output (SPEC 16.3, amendment A2).

Agents never name a model: they ask for a role. Per run, each role resolves to a concrete
LiteLLM catalogue model: task override, then the admin default, then the settings default.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from orchestrix_core.schemas import Agent, CallTag, ModelPrice, Role, Usage

ROLES: Final[tuple[Role, ...]] = ("planner", "specialist", "reviewer", "fast")

# Reasoning depth per role (A2). Sent to every model: the gateway drops it for models that do
# not support it (`drop_params`), so the app never branches on a model family.
REASONING_EFFORT: Final[dict[Role, str]] = {
    "planner": "medium",
    "reviewer": "medium",
    "specialist": "low",
    "fast": "low",
}

DEFAULT_AGENT: Final[dict[Role, Agent]] = {
    "planner": "supervisor",
    "specialist": "research",
    "reviewer": "reviewer",
    "fast": "memory_writer",
}

_THINK_RE: Final = re.compile(r"<think>.*?</think>", re.DOTALL)


def resolve_models(
    overrides: Mapping[str, str],
    admin_defaults: Mapping[str, str],
    settings_defaults: Mapping[str, str],
) -> dict[Role, str]:
    """Resolve every role: task override -> admin default -> settings default."""
    resolved: dict[Role, str] = {}
    for role in ROLES:
        model = overrides.get(role) or admin_defaults.get(role) or settings_defaults.get(role)
        if not model:
            raise ValueError(f"no model configured for role {role!r}")
        resolved[role] = model
    return resolved


def strip_think(text: str) -> str:
    """Drop Qwen-style <think> blocks before parsing (SPEC 16.3)."""
    return _THINK_RE.sub("", text).strip()


COST_HEADER: Final = "x-litellm-response-cost"
# Longer than the gateway's worst case (infra/litellm/config.yaml: timeout 180 s, num_retries 1),
# so a slow first attempt is retried by the gateway instead of failing the run client-side.
GATEWAY_TIMEOUT: Final = 420.0


def response_cost(message: BaseMessage) -> float | None:
    """The gateway's cost for this call, priced from the model's catalogue entry (SPEC 15.3)."""
    metadata: dict[str, Any] = dict(getattr(message, "response_metadata", None) or {})
    headers: dict[str, Any] = dict(metadata.get("headers") or {})
    raw = headers.get(COST_HEADER)
    try:
        return float(raw) if raw not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def usage_of(message: BaseMessage, price: ModelPrice | None) -> Usage:
    """Tokens from the message; cost from the gateway, else from an optional price override."""
    meta: dict[str, Any] = dict(getattr(message, "usage_metadata", None) or {})
    tokens_in = int(meta.get("input_tokens", 0))
    tokens_out = int(meta.get("output_tokens", 0))
    usd = response_cost(message)
    if usd is None:
        usd = 0.0
        if price is not None:
            usd = (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1e6
    return Usage(input_tokens=tokens_in, output_tokens=tokens_out, usd=round(usd, 6))


def served_model(message: BaseMessage) -> str | None:
    """The catalogue model that actually answered (differs from the request after a fallback)."""
    metadata: dict[str, Any] = dict(getattr(message, "response_metadata", None) or {})
    headers: dict[str, Any] = dict(metadata.get("headers") or {})
    model = headers.get("x-litellm-model-group") or metadata.get("model_name")
    return str(model) if model else None


class GatewayChatProvider:
    """ChatProvider backed by the LiteLLM proxy. One instance per run."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        models: Mapping[Role, str],
        prices: Mapping[str, ModelPrice],
        timeout: float = GATEWAY_TIMEOUT,
        run_id: str = "",
        sink: Any = None,  # orchestrix_agents.context.LlmCallSink
        team: str = "",
        local_fallback: bool = True,
    ) -> None:
        self._team = team
        self._local_fallback = local_fallback
        self._base_url = base_url.rstrip("/") + "/v1"
        self._api_key = api_key
        self._models = dict(models)
        self._prices = dict(prices)
        self._timeout = timeout
        self._run_id = run_id
        self._sink = sink

    def model_name(self, role: Role) -> str:
        return self._models[role]

    def price(self, role: Role) -> ModelPrice | None:
        return self._prices.get(self._models[role])

    def chat(self, role: Role, tag: CallTag | None = None) -> BaseChatModel:
        from orchestrix_agents.accounting import LlmCallRecorder

        model = self._models[role]
        tag = tag or CallTag(agent=DEFAULT_AGENT[role], purpose="chat")
        recorder = LlmCallRecorder(
            sink=self._sink,
            run_id=self._run_id,
            role=role,
            requested_model=model,
            tag=tag,
            prices=self._prices,
            team=self._team,
        )
        kwargs: dict[str, Any] = {"reasoning_effort": REASONING_EFFORT[role]}
        if role == "reviewer":
            kwargs["temperature"] = 0
        if not self._local_fallback:
            kwargs["extra_body"] = {"disable_fallbacks": True}  # LiteLLM: no local fallback
        # Retries belong to the gateway (SPEC 16.3): never stack client retries on top.
        return ChatOpenAI(
            model=model,
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=0,
            include_response_headers=True,
            callbacks=[recorder],
            metadata={"orchestrix_role": role, **tag.model_dump()},
            **kwargs,
        )


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    usage: Usage
    model: str | None


class StructuredOutputError(Exception):
    pass


async def invoke_structured[T: BaseModel](
    model: BaseChatModel,
    schema: type[T],
    messages: Sequence[BaseMessage],
    *,
    price: ModelPrice | None = None,
) -> StructuredResult[T]:
    """Structured output with the SPEC 16.3 policy.

    JSON-schema mode first; function calling if the provider rejects it. On a validation
    error, retry once with the error appended, then raise StructuredOutputError.
    """
    usage = Usage()
    history = list(messages)
    error: str | None = None
    for attempt in range(2):
        raw, parsed, error = await _call_structured(model, schema, history)
        if raw is not None:
            usage = usage + usage_of(raw, price)
        if parsed is not None:
            return StructuredResult(
                value=parsed, usage=usage, model=served_model(raw) if raw else None
            )
        if attempt == 0:
            history = [
                *history,
                HumanMessage(
                    content=(
                        "Your previous reply did not match the required JSON schema. "
                        f"Validation error: {error}. Reply again with only valid JSON."
                    )
                ),
            ]
    raise StructuredOutputError(f"{schema.__name__}: {error}")


async def _call_structured[T: BaseModel](
    model: BaseChatModel, schema: type[T], messages: Sequence[BaseMessage]
) -> tuple[AIMessage | None, T | None, str | None]:
    try:
        runnable = model.with_structured_output(schema, method="json_schema", include_raw=True)
        out: Any = await runnable.ainvoke(list(messages))
    except NotImplementedError:
        raise
    except Exception as exc:  # provider rejected json_schema mode: fall back (SPEC 16.3)
        # Some OpenAI-compatible providers accept response_format but return plain text.  Their
        # LangChain parser raises Pydantic ValidationError before exposing ``raw``; this is the
        # same compatibility case as an explicit json_schema rejection, so use function calling.
        if not (_is_schema_rejection(exc) or isinstance(exc, ValidationError)):
            raise
        runnable = model.with_structured_output(schema, method="function_calling", include_raw=True)
        try:
            out = await runnable.ainvoke(list(messages))
        except ValidationError as fallback_exc:
            return None, None, str(fallback_exc)
    raw: AIMessage | None = out.get("raw")
    parsed = out.get("parsed")
    if isinstance(parsed, schema):
        return raw, parsed, None
    # LangChain could not parse: strip <think> blocks and validate ourselves.
    content = raw.content if raw is not None and isinstance(raw.content, str) else ""
    try:
        return raw, schema.model_validate_json(strip_think(content)), None
    except ValidationError as exc:
        return raw, None, str(out.get("parsing_error") or exc)


def _is_schema_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json_schema" in text
