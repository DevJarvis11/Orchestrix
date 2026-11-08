"""Call one MCP tool from inside the Compose network (debugging and stack tests).

    python -m orchestrix_tools.probe sandbox run_python '{"code": "print(1)"}' --run-id <uuid>

Prints the tool result as JSON: {"is_error", "structured", "text"}.
"""

import argparse
import asyncio
import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from orchestrix_core.config import get_settings


async def probe(server: str, tool: str, args: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.mcp_service_token.get_secret_value()}"}
    if run_id:
        headers["X-Run-Id"] = run_id
    async with (
        httpx.AsyncClient(headers=headers, timeout=120) as http,
        streamable_http_client(settings.mcp_urls[server], http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool, args)
    text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
    return {"is_error": result.isError, "structured": result.structuredContent, "text": text}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server")
    parser.add_argument("tool")
    parser.add_argument("args", nargs="?", default="{}")
    parser.add_argument("--run-id")
    a = parser.parse_args()
    print(json.dumps(asyncio.run(probe(a.server, a.tool, json.loads(a.args), a.run_id))))


if __name__ == "__main__":
    main()
