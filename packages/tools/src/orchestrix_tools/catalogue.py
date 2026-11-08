"""The tool catalogue (SPEC 12.1, 12.3): defaults synced into the `tools` table.

Canonical names are `server.tool`. Models see `server_tool`, because OpenAI-style function
names cannot contain dots; `canonical()` maps back.
"""

from dataclasses import dataclass
from typing import Final

from orchestrix_core.schemas import Specialist


@dataclass(frozen=True)
class ToolSpec:
    name: str  # canonical, e.g. "web.search"
    sensitive: bool
    allowed_specialists: tuple[Specialist, ...]
    rate_limit_per_min: int = 60
    side_effecting: bool = False  # writes or sends: tainted subtasks need approval (SPEC 15.2)

    @property
    def server(self) -> str:
        return self.name.split(".", 1)[0]


CATALOGUE: Final[dict[str, ToolSpec]] = {
    t.name: t
    for t in (
        ToolSpec("web.search", False, ("research",)),
        ToolSpec("web.fetch", False, ("research",)),
        ToolSpec("sandbox.run_python", False, ("data_analyst", "coder")),
        ToolSpec("data.describe_table", False, ("data_analyst",)),
        ToolSpec("data.sql_query", False, ("data_analyst",)),
        ToolSpec("files.read_artifact", False, ("research", "data_analyst", "writer", "coder")),
        ToolSpec(
            "files.write_artifact", False, ("data_analyst", "writer", "coder"), side_effecting=True
        ),
        ToolSpec("comms.send_email", True, ("writer",), rate_limit_per_min=10, side_effecting=True),
    )
}

SERVERS: Final[tuple[str, ...]] = ("web", "sandbox", "data", "files", "comms")


def model_name(canonical_name: str) -> str:
    return canonical_name.replace(".", "_", 1)


def canonical(model_facing_name: str) -> str:
    """`data_describe_table` -> `data.describe_table` (server names contain no underscore)."""
    server, _, tool = model_facing_name.partition("_")
    return f"{server}.{tool}"
