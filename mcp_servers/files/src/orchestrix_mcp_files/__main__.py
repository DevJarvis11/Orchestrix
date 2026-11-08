"""Run mcp-files: `python -m orchestrix_mcp_files`."""

import uvicorn

from orchestrix_mcp_files.server import FilesSettings, build_server
from orchestrix_tools.conventions import build_app


def main() -> None:
    settings = FilesSettings()  # pyright: ignore[reportCallIssue]
    app = build_app(build_server(settings), settings.mcp_service_token.get_secret_value())
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
