"""Specialists (SPEC 12.1). M2 runs a stub; M3 builds create_agent specialists with MCP tools."""

from typing import Final

from orchestrix_core.schemas import Specialist

# Allowed tools per specialist (SPEC 12.1). M3 syncs these into tools.allowed_specialists.
SPECIALIST_TOOLS: Final[dict[Specialist, frozenset[str]]] = {
    "research": frozenset({"web.search", "web.fetch", "files.read_artifact"}),
    "data_analyst": frozenset(
        {
            "sandbox.run_python",
            "data.describe_table",
            "data.sql_query",
            "files.read_artifact",
            "files.write_artifact",
        }
    ),
    "writer": frozenset({"files.read_artifact", "files.write_artifact", "comms.send_email"}),
    "coder": frozenset({"sandbox.run_python", "files.read_artifact", "files.write_artifact"}),
}
