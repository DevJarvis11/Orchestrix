"""PII redaction (FR-93, NFR-06): before memory writes and before traces leave the process.

Patterns are configurable per name (ORCHESTRIX_PII_PATTERNS, JSON {name: regex}); a match is
replaced with a typed placeholder such as [EMAIL].
"""

import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Final

DEFAULT_PATTERNS: Final[dict[str, str]] = {
    "EMAIL": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    # card-like: 13-19 digits with optional spaces or dashes
    "CARD": r"\b(?:\d[ -]?){12,18}\d\b",
    # government ids: US SSN, UK NI number, Indian Aadhaar and PAN
    "GOV_ID": r"\b(?:\d{3}-\d{2}-\d{4}|[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]|\d{4}\s\d{4}\s\d{4}|[A-Z]{5}\d{4}[A-Z])\b",
    "PHONE": r"(?<!\w)\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\w)",
}
ORDER: Final = ("EMAIL", "CARD", "GOV_ID", "PHONE")  # most specific first


class Redactor:
    def __init__(self, patterns: Mapping[str, str] | None = None) -> None:
        merged = {**DEFAULT_PATTERNS, **(patterns or {})}
        names = [n for n in ORDER if n in merged] + [n for n in merged if n not in ORDER]
        self._rules = [(n, re.compile(merged[n])) for n in names if merged[n]]

    def text(self, value: str) -> str:
        for name, rule in self._rules:
            value = rule.sub(f"[{name}]", value)
        return value

    def any(self, value: Any) -> Any:
        """Redact strings anywhere inside JSON-like data (trace inputs and outputs)."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.any(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
        if isinstance(value, list | tuple):
            return [self.any(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
        return value


@lru_cache
def default_redactor() -> Redactor:
    from orchestrix_core.config import get_settings

    return Redactor(get_settings().pii_patterns)
