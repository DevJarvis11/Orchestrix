"""Application settings (SPEC 21.1). Every value comes from the environment."""

from functools import lru_cache
from pathlib import Path
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


class Settings(BaseSettings):
    """Orchestrix configuration. Prefix `ORCHESTRIX_` unless an alias says otherwise."""

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
        env_ignore_empty=True,  # Compose passes an unset variable as "": use the default
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
    s3_public_endpoint: str = "http://localhost:59000"  # what browsers use for presigned URLs

    litellm_url: str = "http://localhost:54000"
    litellm_master_key: SecretStr = SecretStr("")
    model_profile: Literal["local", "hybrid", "cloud"] = "hybrid"
    default_models: dict[Role, str] = Field(default_factory=_default_models)
    # A1 falls back from the VPN model to host Ollama. False = VPN models only: LiteLLM gets
    # `disable_fallbacks` and memory rerank has no local CPU fallback (the user's choice).
    local_fallback: bool = True
    # Prices live with each model in infra/litellm/config.yaml (input_cost_per_token), so the
    # gateway's cost header is the source. This is only an override for models it cannot price.
    model_prices: dict[str, ModelPrice] = {}

    embedding_model: str = "embed"
    embedding_dim: int = 2560

    opa_url: str = "http://localhost:58181"

    auth_mode: Literal["oidc", "dev"] = "oidc"
    oidc_issuer: str = "http://localhost:8180/realms/orchestrix"
    oidc_audience: str = "orchestrix-api"
    oidc_jwks_url: str | None = None  # default: <issuer>/protocol/openid-connect/certs
    cors_origins: list[str] = ["http://localhost:5173"]

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
    public_web_url: str = "http://localhost:5173"  # deep links in notifications
    org_email_domain: str = "orchestrix.local"  # OPA: recipients outside it need approval
    escalation_policy_path: Path | None = None  # default: orchestrix_policy's packaged yaml
    clarify_enabled: bool = True  # FR-15: the supervisor may ask before planning
    pii_patterns: dict[str, str] = {}  # FR-93: overrides/additions, name -> regex ("" disables)
    metrics_port: int | None = None  # worker /metrics (SPEC 19.2); the API serves /metrics itself
    mcp_service_token: SecretStr = SecretStr("")
    mcp_urls: dict[str, str] = Field(
        default_factory=lambda: {
            name: f"http://mcp-{name}:8000/mcp"
            for name in ("web", "sandbox", "data", "files", "comms")
        }
    )

    default_budget: Budget = Field(default_factory=Budget)
    plan_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    review_score_threshold: float = Field(default=6, ge=0, le=10)
    max_concurrency: int = Field(default=4, ge=1)
    lease_seconds: int = Field(default=60, ge=5)
    heartbeat_seconds: int = Field(default=15, ge=1)

    trace_retention_days: int = Field(default=30, ge=1)  # NFR-06
    run_retention_days: int = Field(default=180, ge=1)  # NFR-06: checkpoints pruned after this
    graph_version: str = "dev"  # git sha of packages/agents, stamped on every run (SPEC 10)

    # Dev/test only: locally signed JWTs (auth_mode=dev) and deterministic fake models.
    dev_jwt_secret: SecretStr = SecretStr("")
    fake_models: bool = False
    fake_model_delay_seconds: float = Field(default=0, ge=0)

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
        if self.fake_models and self.env == "production":
            raise ValueError("ORCHESTRIX_FAKE_MODELS is only allowed in development and test")
        if self.auth_mode == "dev" and len(self.dev_jwt_secret.get_secret_value()) < 32:
            raise ValueError(
                "ORCHESTRIX_DEV_JWT_SECRET must be at least 32 characters in dev auth mode"
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
