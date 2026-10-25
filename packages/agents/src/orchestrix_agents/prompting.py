"""Versioned Jinja2 prompt templates (SPEC 9: prompts live in files, never inline)."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - plain-text LLM prompts, never rendered as HTML
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render_prompt(name: str, **values: Any) -> str:
    return _env().get_template(name).render(**values)
