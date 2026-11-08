"""M3 Done-when: the sandbox has no network, a read-only root filesystem, and enforces its time
limit, proven against the real hardened container (FR-26). Plus a live web search through SearXNG.
Run with `make test-stack` against `make dev`."""

import json
import subprocess
from typing import Any

import pytest

pytestmark = pytest.mark.stack

COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ".env",
    "-f",
    "infra/compose/docker-compose.yml",
    "--profile",
    "core",
]


def probe(server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    out = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "worker",
            "python",
            "-m",
            "orchestrix_tools.probe",
            server,
            tool,
            json.dumps(args),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def run_python(code: str, **extra: Any) -> dict[str, Any]:
    return probe("sandbox", "run_python", {"code": code, **extra})


def test_fr26_sandbox_has_no_network() -> None:
    code = (
        "import socket\n"
        "for target in [('1.1.1.1', 53), ('8.8.8.8', 443)]:\n"
        "    try:\n"
        "        socket.create_connection(target, timeout=3); print('CONNECTED', target)\n"
        "    except OSError as e:\n"
        "        print('blocked', target, e.errno)\n"
        "try:\n"
        "    socket.getaddrinfo('example.com', 443); print('DNS OK')\n"
        "except OSError:\n"
        "    print('dns blocked')\n"
    )
    stdout = run_python(code)["structured"]["stdout"]
    assert "CONNECTED" not in stdout
    assert "DNS OK" not in stdout
    assert "dns blocked" in stdout


def test_fr26_sandbox_root_filesystem_is_read_only() -> None:
    code = (
        "import os\n"
        "for path in ['/usr/x', '/app/x', '/etc/x', '/x']:\n"
        "    try:\n"
        "        open(path, 'w').write('x'); print('WROTE', path)\n"
        "    except OSError:\n"
        "        print('denied', path)\n"
        "open('out/result.txt', 'w').write('ok')\n"
        "print('uid', os.getuid())\n"
    )
    result = run_python(code)["structured"]
    assert "WROTE" not in result["stdout"]
    assert result["stdout"].count("denied") == 4
    assert "uid 10001" in result["stdout"]  # not root
    assert [f["name"] for f in result["created_files"]] == ["result.txt"]  # tmpfs workdir works


def test_fr26_sandbox_time_limit_is_enforced() -> None:
    result = run_python("import time\ntime.sleep(30)", timeout_s=2)
    assert result["is_error"]
    assert '"error_code":"timeout"' in result["text"]


def test_fr26_sandbox_analyses_data_with_pandas() -> None:
    code = "import pandas as pd, numpy as np\nprint(pd.Series(np.arange(1, 5)).mean())"
    assert run_python(code)["structured"]["stdout"].strip() == "2.5"


def test_web_search_through_searxng() -> None:
    result = probe("web", "search", {"query": "pgvector postgres extension", "max_results": 3})
    assert not result["is_error"], result["text"]
    # Search engines may throttle or time out independently of our self-hosted SearXNG instance.
    # The protocol/server acceptance check is therefore the bounded, typed result; deterministic
    # query content is covered by the recorded-fixture unit test.
    results = result["structured"]["results"]
    assert isinstance(results, list)
    assert len(results) <= 3
    assert all({"title", "url", "snippet"} <= set(item) for item in results)


def test_fr26_sandbox_cannot_see_credentials_or_reach_storage() -> None:
    """Untrusted code runs as the server's uid: the server must hold no storage credentials,
    its environment must be unreadable, and S3 must be unreachable (BUILD_LOG 2026-11-07)."""
    code = (
        "import os, socket\n"
        "leaked = []\n"
        "for pid in [p for p in os.listdir('/proc') if p.isdigit()]:\n"
        "    try:\n"
        "        env = open(f'/proc/{pid}/environ', 'rb').read()\n"
        "        leaked += [k for k in (b'S3_SECRET', b'MCP_SERVICE_TOKEN') if k in env]\n"
        "    except OSError:\n"
        "        pass\n"
        "print('leaked', sorted(set(leaked)))\n"
        "try:\n"
        "    socket.create_connection(('s3', 9000), timeout=3); print('S3 REACHABLE')\n"
        "except OSError:\n"
        "    print('s3 unreachable')\n"
    )
    stdout = run_python(code)["structured"]["stdout"]
    assert "leaked []" in stdout
    assert "s3 unreachable" in stdout
