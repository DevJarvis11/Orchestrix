"""Settings loading (SPEC 21.1) and the model-routing defaults (amendment A2)."""

import pytest
from pydantic import ValidationError

from orchestrix_core.config import Settings, libpq_dsn
from orchestrix_core.schemas import Budget

DSN = "postgresql+psycopg://orchestrix_app:pw@postgres:5432/orchestrix"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith(("ORCHESTRIX_", "OLLAMA_", "LANGFUSE_", "TAVILY_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("ORCHESTRIX_DATABASE_URL", DSN)


def load() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_defaults_match_spec() -> None:
    s = load()
    assert s.env == "development"
    assert s.auth_mode == "oidc"
    assert s.plan_confidence_threshold == 0.6
    assert s.review_score_threshold == 6
    assert s.max_concurrency == 4
    assert (s.lease_seconds, s.heartbeat_seconds) == (60, 15)
    assert s.embedding_dim == 2560


def test_fr63_default_budget() -> None:
    assert load().default_budget == Budget(
        max_tokens=400_000, max_usd=2.0, max_seconds=1800, max_tool_calls=60, max_steps=150
    )


def test_prefixed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRIX_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("ORCHESTRIX_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("ORCHESTRIX_DEFAULT_BUDGET", '{"max_usd": 0.5}')
    s = load()
    assert s.redis_url == "redis://redis:6379/0"
    assert s.max_concurrency == 2
    assert s.default_budget.max_usd == 0.5
    assert s.default_budget.max_tokens == 400_000


def test_unprefixed_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    s = load()
    assert s.ollama_base_url == "http://host.docker.internal:11434"
    assert s.langfuse_host == "http://langfuse-web:3000"
    assert s.tavily_api_key is not None
    assert s.tavily_api_key.get_secret_value() == "tvly-test"


def test_secrets_are_masked() -> None:
    s = load()
    assert "pw" not in repr(s)
    assert s.database_url.get_secret_value() == DSN


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRIX_DATABASE_URL")
    with pytest.raises(ValidationError, match="database_url"):
        load()


@pytest.mark.parametrize("env", ["test", "production"])
def test_dev_auth_refused_outside_development(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("ORCHESTRIX_ENV", env)
    monkeypatch.setenv("ORCHESTRIX_AUTH_MODE", "dev")
    with pytest.raises(ValidationError, match="AUTH_MODE=dev"):
        load()


def test_dev_auth_allowed_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRIX_AUTH_MODE", "dev")
    assert load().auth_mode == "dev"


def test_heartbeat_must_be_shorter_than_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRIX_HEARTBEAT_SECONDS", "60")
    with pytest.raises(ValidationError, match="heartbeat_seconds"):
        load()


def test_checkpointer_dsn_drops_driver_suffix() -> None:
    assert load().checkpointer_dsn == "postgresql://orchestrix_app:pw@postgres:5432/orchestrix"
    assert libpq_dsn("postgresql://x") == "postgresql://x"


def test_migrations_url_prefers_migrator(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load().migrations_url == DSN
    migrator = "postgresql+psycopg://orchestrix_migrator:pw@postgres:5432/orchestrix"
    monkeypatch.setenv("ORCHESTRIX_MIGRATOR_DATABASE_URL", migrator)
    assert load().migrations_url == migrator


def test_default_models_every_role_on_remote_model() -> None:
    assert load().default_models == {
        "planner": "gpt-oss-120b",
        "specialist": "gpt-oss-120b",
        "reviewer": "gpt-oss-120b",
        "fast": "gpt-oss-120b",
    }


def test_partial_default_models_merge_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRIX_DEFAULT_MODELS", '{"planner": "qwen3-8b-local"}')
    models = load().default_models
    assert models["planner"] == "qwen3-8b-local"
    assert models["reviewer"] == "gpt-oss-120b"


def test_default_models_reject_unknown_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRIX_DEFAULT_MODELS", '{"boss": "gpt-oss-120b"}')
    with pytest.raises(ValidationError):
        load()


def test_notional_prices_cover_catalogue_models() -> None:
    prices = load().model_prices
    assert {"gpt-oss-120b", "qwen3-8b-local", "embed", "rerank"} <= prices.keys()
