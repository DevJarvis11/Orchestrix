"""mcp-sandbox: sandbox.run_python (SPEC 12.3, 12.4).

The sandbox holds no storage credentials and has no route to S3: the worker resolves the
`artifact_ids` the model asked for (checking the run may read them), sends their bytes in
`input_files`, and stores the files this tool returns in `created_files`. Untrusted code
therefore never shares a process tree with anything worth stealing.
"""

import base64
import binascii
import hashlib
import mimetypes
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from orchestrix_mcp_sandbox.executor import run_python, unique_names
from orchestrix_tools.conventions import new_server, tool_error
from orchestrix_tools.server_settings import ServerSettings

MAX_INPUT_BYTES = 100 * 1024 * 1024  # ten attachments at the 10 MB upload cap


class SandboxSettings(ServerSettings):
    sandbox_workdir: Path = Path("/work")  # tmpfs in the container


class InputFile(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_b64: str


class CreatedFile(BaseModel):
    name: str
    mime: str
    bytes: int
    sha256: str
    content_b64: str


class RunResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    created_files: list[CreatedFile]


def build_server(settings: SandboxSettings) -> FastMCP:
    mcp = new_server(
        "sandbox",
        "Run Python 3 with pandas, numpy, scipy and matplotlib. No network. Input files are in "
        "./in (read-only); write result files to ./out.",
    )

    @mcp.tool(
        name="run_python",
        description=(
            "Run a Python script. Files for artifact_ids appear in ./in; files written to ./out "
            "are stored as new artifacts."
        ),
    )
    async def run(  # pyright: ignore[reportUnusedFunction]
        code: Annotated[str, Field(min_length=1, max_length=100_000)],
        artifact_ids: Annotated[list[str], Field(max_length=10)] = [],  # noqa: B006
        timeout_s: Annotated[int, Field(ge=1, le=60)] = 30,
        # Filled by the worker from artifact_ids; hidden from the model's schema.
        input_files: Annotated[list[InputFile], Field(max_length=10)] = [],  # noqa: B006
    ) -> RunResult:
        if len(input_files) < len(artifact_ids):
            raise tool_error("inputs_missing", "the platform did not supply the requested files")
        inputs: dict[str, bytes] = {}
        total = 0
        names = unique_names([f.name for f in input_files])
        for f, name in zip(input_files, names, strict=True):
            try:
                body = base64.b64decode(f.content_b64, validate=True)
            except binascii.Error as exc:
                raise tool_error("bad_input", f"input {f.name!r} is not valid base64") from exc
            total += len(body)
            if total > MAX_INPUT_BYTES:
                raise tool_error("too_large", "input files exceed 100 MB")
            inputs[name] = body
        execution = await run_python(code, inputs, timeout_s, settings.sandbox_workdir)
        if execution.timed_out:
            raise tool_error("timeout", f"script exceeded {timeout_s}s and was killed")
        created = [
            CreatedFile(
                name=name,
                mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                content_b64=base64.b64encode(body).decode(),
            )
            for name, body in execution.outputs.items()
        ]
        return RunResult(
            stdout=execution.stdout,
            stderr=execution.stderr,
            exit_code=execution.exit_code,
            created_files=created,
        )

    return mcp
