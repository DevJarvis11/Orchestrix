"""Run mcp-sandbox: `python -m orchestrix_mcp_sandbox`."""

import ctypes
import ctypes.util

import uvicorn

from orchestrix_mcp_sandbox.server import SandboxSettings, build_server
from orchestrix_tools.conventions import build_app

PR_SET_DUMPABLE = 4


def not_dumpable() -> None:
    """Defence in depth: hide this process's /proc/<pid>/environ and memory from the
    untrusted children, which run as the same uid (execve resets the flag for them)."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE, 0) failed")


def main() -> None:
    not_dumpable()
    settings = SandboxSettings()  # pyright: ignore[reportCallIssue]
    app = build_app(build_server(settings), settings.mcp_service_token.get_secret_value())
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
