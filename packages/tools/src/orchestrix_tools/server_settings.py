"""Settings shared by every MCP server: env only (SPEC 21.1 prefix ORCHESTRIX_)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORCHESTRIX_", extra="ignore")

    mcp_service_token: SecretStr
    port: int = 8000
