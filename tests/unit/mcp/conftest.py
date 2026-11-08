"""Helpers for MCP server tests: call tools through the real protocol, in memory."""

import json
import socket
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent


async def call(server: FastMCP, tool: str, args: dict[str, Any]) -> CallToolResult:
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(tool, args)


def error_of(result: CallToolResult) -> dict[str, Any]:
    """The structured {error_code, message, retryable} after FastMCP's 'Error executing tool' prefix."""
    assert result.isError, result
    block = result.content[0]
    assert isinstance(block, TextContent)
    return json.loads(block.text[block.text.index("{") :])


@pytest.fixture
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """hostname -> IP for socket.getaddrinfo, so the SSRF guard can be exercised offline."""
    table: dict[str, str] = {}

    def getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        if host not in table:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table
