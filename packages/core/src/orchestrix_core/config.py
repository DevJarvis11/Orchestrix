"""Application settings (SPEC 21.1). Every value comes from the environment."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from orchestrix_core.schemas import Budget, ModelPrice, Role


def libpq_dsn(sqlalchemy_url: str) -> str:
    """Drop the SQLAlchemy driver suffix: psycopg and the checkpointer take a plain libpq DSN."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _default_models() -> dict[Role, str]:
    return {
        "planner": "gpt-oss-120b",
        "specialist": "gpt-oss-120b",
        "reviewer": "gpt-oss-120b",
        "fast": "gpt-oss-120b",
    }


def _default_prices() -> dict[str, ModelPrice]:
    # Notional prices for self-hosted models so budgets work offline (improvement 8).
    return {
        "gpt-oss-120b": ModelPrice(input_per_mtok=0.15, output_per_mtok=0.60),
        "qwen3-8b-local": ModelPrice(input_per_mtok=0.05, output_per_mtok=0.20),
        "embed": ModelPrice(input_per_mtok=0.01, output_per_mtok=0.0),
        "rerank": ModelPrice(input_per_mtok=0.01, output_per_mtok=0.0),
    }


class Settings(BaseSettings):
    """Orchestrix configuration. Prefix `ORCHESTRIX_` unless an alias says otherwise."""

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
    )

    env: Literal["development", "test", "production"] = "development"

    database_url: SecretStr = Field(
        description="App role DSN, e.g. postgresql+psycopg://orchestrix_app:...@postgres:5432/orchestrix"
    )
    migrator_database_url: SecretStr | None = Field(
        default=None, description="Migrator role DSN used by Alembic; falls back to database_url"
    )
    redis_url: str = "redis://localhost:56379/0"

    s3_endpoint: str = "http://localhost:59000"
    s3_bucket: str = "orchestrix"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")

    litellm_url: str = "http://localhost:54000"
    litellm_master_key: SecretStr = SecretStr("")
    model_profile: Literal["local", "hybrid", "cloud"] = "hybrid"
    ollama_base_url: str = Field(
        default="http://host.docker.internal:11434", validation_alias="OLLAMA_BASE_URL"
    )
    default_models: dict[Role, str] = Field(default_factory=_default_models)
    model_prices: dict[str, ModelPrice] = Field(default_factory=_default_prices)

    embedding_model: str = "embed"
    embedding_dim: int = 2560

    opa_url: str = "http://localhost:58181"

    auth_mode: Literal["oidc", "dev"] = "oidc"
    oidc_issuer: str = "http://localhost:8180/realms/orchestrix"
    oidc_audience: str = "orchestrix-api"

    langfuse_host: str | None = Field(default=None, validation_alias="LANGFUSE_HOST")
    langfuse_public_key: SecretStr | None = Field(
        default=None, validation_alias="LANGFUSE_PUBLIC_KEY"
    )
    langfuse_secret_key: SecretStr | None = Field(
        default=None, validation_alias="LANGFUSE_SECRET_KEY"
    )

    tavily_api_key: SecretStr | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    searxng_url: str = "http://localhost:58888"
    slack_webhook_url: SecretStr | None = None
    mcp_service_token: SecretStr = SecretStr("")

    default_budget: Budget = Field(default_factory=Budget)
    plan_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    review_score_threshold: float = Field(default=6, ge=0, le=10)
    max_concurrency: int = Field(default=4, ge=1)
    lease_seconds: int = Field(default=60, ge=5)
    heartbeat_seconds: int = Field(default=15, ge=1)

    @field_validator("default_models", mode="after")
    @classmethod
    def _merge_default_models(cls, value: dict[Role, str]) -> dict[Role, str]:
        # A partial override (e.g. only the planner) keeps the defaults for the other roles.
        return {**_default_models(), **value}

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.auth_mode == "dev" and self.env != "development":
            raise ValueError(
                "ORCHESTRIX_AUTH_MODE=dev is only allowed when ORCHESTRIX_ENV=development"
            )
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        return self

    @property
    def checkpointer_dsn(self) -> str:
        """The app DSN in plain libpq form, as the LangGraph checkpointer expects."""
        return libpq_dsn(self.database_url.get_secret_value())

    @property
    def migrations_url(self) -> str:
        """DSN Alembic runs with: the migrator role when configured."""
        url = self.migrator_database_url or self.database_url
        return url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return Settings()  # pyright: ignore[reportCallIssue]  # required fields come from the env
