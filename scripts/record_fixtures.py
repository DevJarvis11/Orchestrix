"""Record scrubbed web and model responses used for deterministic offline tests and demos.

The command intentionally talks only to this project's SearXNG and LiteLLM endpoints; it never
prints credentials or raw responses.  It requires the core stack to be running:

    make record-fixtures
"""

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr

from orchestrix_agents.llm import GatewayChatProvider
from orchestrix_core.config import get_settings
from orchestrix_mcp_web.server import WebSettings, build_server

ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "tests" / "fixtures"
SEARCHES: tuple[tuple[str, int], ...] = (
    ("qdrant license github benchmarks", 8),
    ("milvus license github benchmarks", 8),
    ("weaviate license github benchmarks", 8),
    ("pgvector license github benchmarks", 8),
)
_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|password|secret|token)\b"
    r"(\s*[:=]\s*[\"']?)[^\s,\"'}]+"
)


def scrub(value: Any) -> Any:
    """Remove common secret-shaped values recursively before they reach the repository."""
    if isinstance(value, str):
        return _SECRET.sub(r"\1\2[REDACTED]", value)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {str(key): scrub(item) for key, item in value.items()}
    return value


async def record_web(output: Path, searxng_url: str, pages_per_query: int) -> list[Path]:
    """Drive mcp-web itself in record mode, so fixtures match exactly what the tool returns:
    each search, then its top pages through web.fetch (with the SSRF guard)."""
    settings = WebSettings(  # pyright: ignore[reportCallIssue]
        mcp_service_token=SecretStr("recording-only-token"),
        searxng_url=searxng_url,
        web_fixtures_dir=output / "web",
        web_record_fixtures=True,
    )
    server = build_server(settings)
    before = set((output / "web").rglob("*.json"))
    async with create_connected_server_and_client_session(server) as session:
        for query, max_results in SEARCHES:
            found = await session.call_tool("search", {"query": query, "max_results": max_results})
            results: list[dict[str, Any]] = (found.structuredContent or {}).get("results", [])
            for item in results[:pages_per_query]:
                await session.call_tool("fetch", {"url": item["url"], "max_chars": 20000})
    paths = sorted(set((output / "web").rglob("*.json")) - before)
    for path in paths:  # scrub what was written
        path.write_text(
            json.dumps(scrub(json.loads(path.read_text())), indent=2, sort_keys=True) + "\n"
        )
    return paths


async def record_model(output: Path) -> Path:
    settings = get_settings()
    provider = GatewayChatProvider(
        base_url=settings.litellm_url,
        api_key=settings.litellm_master_key,
        models=settings.default_models,
        prices=settings.model_prices,
    )
    response = await provider.chat("specialist").ainvoke(
        [
            HumanMessage(
                content=(
                    "This is a fixture-recording health check. Reply with the exact text "
                    "ORCHESTRIX_FIXTURE and do not reveal or request credentials."
                )
            )
        ]
    )
    path = output / "models" / "specialist_smoke.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            scrub(
                {
                    "model": provider.model_name("specialist"),
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                }
            ),
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    return path


async def main_async(output: Path, pages: int) -> None:
    settings = get_settings()
    paths = await record_web(output, settings.searxng_url, pages)
    paths.append(await record_model(output))
    print("Recorded scrubbed fixtures:")
    for path in paths:
        print(path.relative_to(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pages", type=int, default=2, help="pages fetched per search")
    args = parser.parse_args()
    asyncio.run(main_async(args.output.resolve(), args.pages))


if __name__ == "__main__":
    main()
