"""Typed access to JSON from the API (keeps pyright strict without casts everywhere)."""

from typing import Any, cast


def obj(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def seq(value: object) -> list[Any]:
    return list(cast(list[Any], value)) if isinstance(value, list) else []
