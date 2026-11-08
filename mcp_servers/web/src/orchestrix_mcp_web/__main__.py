"""Run mcp-web: `python -m orchestrix_mcp_web`."""

import uvicorn

from orchestrix_mcp_web.server import WebSettings, build_server
from orchestrix_tools.conventions import build_app


def main() -> None:
    settings = WebSettings()  # pyright: ignore[reportCallIssue]
    app = build_app(build_server(settings), settings.mcp_service_token.get_secret_value())
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
