"""Worker MCP headers (SPEC 12.4): deterministic W3C trace context for every tool call."""

import re

from orchestrix_tools.client import traceparent_for


def test_traceparent_is_w3c_shaped_and_stable_per_tool_call() -> None:
    first = traceparent_for("run-1", "call-1")
    assert first == traceparent_for("run-1", "call-1")
    assert first != traceparent_for("run-1", "call-2")
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", first)
