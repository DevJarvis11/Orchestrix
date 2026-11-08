"""mcp-sandbox process limits (FR-26). Container isolation is proven against the stack separately."""

import base64
import time
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import SecretStr

from orchestrix_mcp_sandbox.executor import run_python
from orchestrix_mcp_sandbox.server import SandboxSettings, build_server
from tests.unit.mcp.conftest import call, error_of


async def test_fr26_runs_code_with_inputs_and_collects_outputs(tmp_path: Path) -> None:
    code = "import pandas as pd\ndf = pd.read_csv('in/lat.csv')\nprint(df['ms'].mean())\ndf.describe().to_csv('out/summary.csv')"
    ex = await run_python(code, {"lat.csv": b"ms\n10\n20\n30\n"}, 30, tmp_path)
    assert ex.exit_code == 0, ex.stderr
    assert ex.stdout.strip() == "20.0"
    assert b"mean" in ex.outputs["summary.csv"]
    assert not any(tmp_path.iterdir())  # per-call workdir removed  # noqa: ASYNC240


async def test_fr26_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    code = "import os, time\nif os.fork() == 0:\n    time.sleep(60)\ntime.sleep(60)"
    start = time.monotonic()
    ex = await run_python(code, {}, 2, tmp_path)
    assert ex.timed_out
    assert time.monotonic() - start < 10


async def test_fr26_cpu_limit_stops_busy_loops(tmp_path: Path) -> None:
    ex = await run_python("while True:\n    pass", {}, 1, tmp_path)
    assert ex.timed_out or ex.exit_code != 0


async def test_fr26_memory_is_capped(tmp_path: Path) -> None:
    ex = await run_python("x = bytearray(3 * 1024**3)\nprint('allocated')", {}, 20, tmp_path)
    assert ex.exit_code != 0
    assert "MemoryError" in ex.stderr


async def test_fr26_environment_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHESTRIX_S3_SECRET_KEY", "do-not-leak")
    ex = await run_python("import os\nprint(sorted(os.environ))", {}, 10, tmp_path)
    assert "ORCHESTRIX_S3_SECRET_KEY" not in ex.stdout
    assert "do-not-leak" not in ex.stdout


async def test_input_names_cannot_escape_the_workdir(tmp_path: Path) -> None:
    ex = await run_python(
        "import os\nprint(os.listdir('in'))", {"../../etc/evil": b"x"}, 10, tmp_path
    )
    assert ex.stdout.strip() == "['evil']"


def sandbox(tmp_path: Path) -> FastMCP:
    settings = SandboxSettings(mcp_service_token=SecretStr("t" * 32), sandbox_workdir=tmp_path)  # type: ignore[call-arg]
    return build_server(settings)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_tool_reads_input_files_and_returns_created_files(tmp_path: Path) -> None:
    """The sandbox holds no storage credentials: bytes come in and go out with the call."""
    code = "open('out/report.md','w').write(open('in/lat.csv').read())"
    result = await call(
        sandbox(tmp_path),
        "run_python",
        {
            "code": code,
            "artifact_ids": ["a1"],
            "input_files": [{"name": "lat.csv", "content_b64": b64(b"ms\n5\n")}],
        },
    )
    assert not result.isError
    [created] = (result.structuredContent or {})["created_files"]
    assert (created["name"], created["mime"]) == ("report.md", "text/markdown")
    assert base64.b64decode(created["content_b64"]) == b"ms\n5\n"


async def test_requested_artifacts_without_supplied_files_are_refused(tmp_path: Path) -> None:
    result = await call(
        sandbox(tmp_path), "run_python", {"code": "print(1)", "artifact_ids": ["a1"]}
    )
    assert error_of(result)["error_code"] == "inputs_missing"


async def test_tool_timeout_is_a_structured_error(tmp_path: Path) -> None:
    result = await call(
        sandbox(tmp_path), "run_python", {"code": "import time; time.sleep(5)", "timeout_s": 1}
    )
    assert error_of(result)["error_code"] == "timeout"


async def test_tool_rejects_timeouts_over_60s(tmp_path: Path) -> None:
    result = await call(sandbox(tmp_path), "run_python", {"code": "print(1)", "timeout_s": 61})
    assert result.isError
