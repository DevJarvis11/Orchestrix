"""LLM judge (SPEC 20.2): rubric scores and position-debiased pairwise preferences, through the
gateway's `reviewer` role (use a cloud/hybrid model for headline numbers)."""

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from orchestrix_agents.context import ChatProvider
from orchestrix_agents.llm import GatewayChatProvider, invoke_structured, resolve_models
from orchestrix_core.config import get_settings
from orchestrix_core.schemas import CallTag, Role

ROLES: tuple[Role, ...] = ("planner", "specialist", "reviewer", "fast")
MAX_CHARS = 24_000  # keep judge prompts inside the 16K-token fallback context (A2)


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).parent / "prompts"),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - plain-text LLM prompts
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(name: str, **values: Any) -> str:
    return _env().get_template(name).render(**values)


def parse_models(spec: str | None) -> dict[str, str]:
    """`gpt-oss-120b` (every role), a role->model JSON object, or empty/`default` (stack defaults)."""
    if not spec or spec == "default":
        return {}
    if spec.lstrip().startswith("{"):
        data: dict[str, Any] = json.loads(spec)
        unknown = set(data) - set(ROLES)
        if unknown:
            raise ValueError(f"unknown roles: {sorted(unknown)}")
        return {str(k): str(v) for k, v in data.items()}
    return dict.fromkeys(ROLES, spec)


def models_label(models: dict[str, str]) -> str:
    if not models:
        return "default"
    values = set(models.values())
    if len(values) == 1 and set(models) == set(ROLES):
        return next(iter(values))
    return ",".join(f"{r}={m}" for r, m in sorted(models.items()))


def provider(models: dict[str, str]) -> GatewayChatProvider:
    settings = get_settings()
    defaults = {str(r): m for r, m in settings.default_models.items()}
    return GatewayChatProvider(
        base_url=settings.litellm_url,
        api_key=settings.litellm_master_key,
        models=resolve_models(models, {}, defaults),
        prices=settings.model_prices,
    )


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0, le=10)
    passed: bool
    rationale: str


class Preference(BaseModel):
    winner: Literal["A", "B", "tie"]
    rationale: str


class Judge:
    def __init__(self, llm: ChatProvider, *, seed: int = 11) -> None:
        self._llm = llm
        self._rng = random.Random(seed)  # noqa: S311 - position shuffling, not security

    @property
    def model(self) -> str:
        return self._llm.model_name("reviewer")

    async def score(self, request: str, rubric: list[str], deliverable: str) -> JudgeVerdict:
        prompt = render(
            "judge_rubric.j2", request=request, rubric=rubric, deliverable=deliverable[:MAX_CHARS]
        )
        result = await invoke_structured(
            self._llm.chat("reviewer", CallTag(agent="reviewer", purpose="review")),
            JudgeVerdict,
            [HumanMessage(prompt)],
        )
        return result.value

    async def prefer(
        self, request: str, first: str, second: str, *, what: str = "answers"
    ) -> Literal["first", "second", "tie"]:
        """Randomises which candidate is shown as A to reduce position bias (SPEC 20.2)."""
        swapped = self._rng.random() < 0.5
        a, b = (second, first) if swapped else (first, second)
        result = await invoke_structured(
            self._llm.chat("reviewer", CallTag(agent="reviewer", purpose="review")),
            Preference,
            [
                HumanMessage(
                    render(
                        "judge_pairwise.j2",
                        what=what,
                        request=request,
                        a=a[: MAX_CHARS // 2],
                        b=b[: MAX_CHARS // 2],
                    )
                )
            ],
        )
        if result.value.winner == "tie":
            return "tie"
        a_won = result.value.winner == "A"
        return "first" if a_won != swapped else "second"
