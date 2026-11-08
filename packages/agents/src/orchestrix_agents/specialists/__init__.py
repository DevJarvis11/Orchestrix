"""Specialists (SPEC 12.1): allow-lists come from the tool catalogue, the single source of truth."""

from typing import Final

from orchestrix_core.schemas import SPECIALISTS, Specialist
from orchestrix_tools.catalogue import CATALOGUE

SPECIALIST_TOOLS: Final[dict[Specialist, frozenset[str]]] = {
    s: frozenset(name for name, spec in CATALOGUE.items() if s in spec.allowed_specialists)
    for s in SPECIALISTS
}
