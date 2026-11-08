"""Reviewer chat model call (FR-44): the `fast` role through the gateway, no tools, free text.

Its answer never drives control flow (it is shown to a human), so plain chat completion is
enough. The call is accounted in llm_calls like every other model call (SPEC 15.3).
"""

from decimal import Decimal
from pathlib import Path
from string import Template
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select

from orchestrix_agents.llm import GATEWAY_TIMEOUT
from orchestrix_api.deps import AppResources
from orchestrix_core.db.models import LlmCall, ModelDefault

PROMPT = Template((Path(__file__).parent / "prompts" / "escalation_chat.txt").read_text())


async def fast_model(res: AppResources) -> str:
    async with res.sessions() as s:
        admin = (
            await s.execute(select(ModelDefault.model).where(ModelDefault.role == "fast"))
        ).scalar_one_or_none()
    return admin or res.settings.default_models["fast"]


async def answer(
    res: AppResources, run_id: UUID, snapshot: str, history: list[dict[str, str]]
) -> str:
    model = await fast_model(res)
    started = monotonic()
    response = await res.http.post(
        f"{res.settings.litellm_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {res.settings.litellm_master_key.get_secret_value()}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": PROMPT.substitute(snapshot=snapshot)},
                *history,
            ],
            "temperature": 0,
            **({} if res.settings.local_fallback else {"disable_fallbacks": True}),
        },
        timeout=GATEWAY_TIMEOUT,
    )
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    usage: dict[str, Any] = body.get("usage") or {}
    served = response.headers.get("x-litellm-model-group") or model
    tokens_in, tokens_out = (
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
    )
    price = res.settings.model_prices.get(served)
    header_cost = response.headers.get("x-litellm-response-cost")
    cost = (
        float(header_cost)
        if header_cost not in (None, "", "None")
        else (
            (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000
            if price
            else 0.0
        )
    )
    async with res.sessions.begin() as s:
        s.add(
            LlmCall(
                run_id=run_id,
                agent="supervisor",
                model_alias="fast",
                model_id=served,
                requested_model=model,
                purpose="chat",
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=Decimal(str(round(cost, 6))),
                latency_ms=int((monotonic() - started) * 1000),
            )
        )
    choices: list[dict[str, Any]] = body.get("choices") or [{}]
    message: dict[str, Any] = choices[0].get("message") or {}
    content: Any = message.get("content") or ""
    return str(content).strip()
