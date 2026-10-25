"""SQLAlchemy models for every application table (SPEC 10, improvement 4, amendment A3).

Alembic owns this schema. LangGraph's checkpointer tables are not modelled here.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 2560
"""Must equal the embedding model's dimension: Yotta qwen3-embed (SPEC 13.5, amendment A5).

Stored as halfvec: pgvector's HNSW index supports up to 2000 dims for vector, 4000 for halfvec.
"""


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sa_text("uuidv7()"))


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _fk(target: str, *, nullable: bool = False, ondelete: str | None = None) -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


class Base(DeclarativeBase):
    pass


# Identity (mirrors Keycloak; Keycloak is the source of truth)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    keycloak_sub: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    monthly_budget_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=sa_text("100")
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )


# Tasks and runs


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("requester_id", "idempotency_key"),
        CheckConstraint("length(description) <= 8000", name="tasks_description_check"),
    )

    id: Mapped[uuid.UUID] = _pk()
    team_id: Mapped[uuid.UUID] = _fk("teams.id")
    requester_id: Mapped[uuid.UUID] = _fk("users.id")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # FR-01, see state_machine.py
    require_plan_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )  # A3: role -> gateway model
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    final_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("kind IN ('main','replay')", name="runs_kind_check"),
        Index("runs_lease_idx", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    head_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[uuid.UUID] = _fk("tasks.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    replay_of_run_id: Mapped[uuid.UUID | None] = _fk("runs.id", nullable=True)
    replay_from_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reclaim_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )  # crash-loop guard: reclaimed by the reaper this many times (SPEC 8.2 step 5)
    graph_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_config_: Mapped[dict[str, Any] | None] = mapped_column(
        "model_config", JSONB
    )  # A3: resolved role -> model snapshot
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


# Plans and subtasks


class PlanRow(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("run_id", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=sa_text("'{}'")
    )
    edited_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    created_at: Mapped[datetime] = _created_at()


class SubtaskRow(Base):
    __tablename__ = "subtasks"
    __table_args__ = (UniqueConstraint("run_id", "plan_version", "key"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    specialist: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    revisions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ReviewRow(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    subtask_id: Mapped[uuid.UUID | None] = _fk("subtasks.id", nullable=True)  # null = final
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(REAL, nullable=False)
    issues: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


# Tool calls and LLM calls (cost accounting)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    subtask_id: Mapped[uuid.UUID | None] = _fk("subtasks.id", nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    server: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_preview: Mapped[str | None] = mapped_column(Text)
    output_object_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    policy_decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("llm_calls_run_idx", "run_id"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    model_alias: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)  # the model that actually served
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


# Human-in-the-loop


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (UniqueConstraint("run_id", "interrupt_id"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    interrupt_id: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_team_id: Mapped[uuid.UUID | None] = _fk("teams.id", nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class EscalationAction(Base):
    __tablename__ = "escalation_actions"

    id: Mapped[uuid.UUID] = _pk()
    escalation_id: Mapped[uuid.UUID] = _fk("escalations.id")
    actor_id: Mapped[uuid.UUID] = _fk("users.id")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class EscalationMessage(Base):
    """Reviewer chat about a pending escalation (FR-44, improvement 4)."""

    __tablename__ = "escalation_messages"
    __table_args__ = (
        CheckConstraint("role IN ('reviewer','agent')", name="escalation_messages_role_check"),
        Index("escalation_messages_escalation_idx", "escalation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    escalation_id: Mapped[uuid.UUID] = _fk("escalations.id")
    author_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)  # null = the agent
    role: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


# Long-term memory (SPEC 13)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('episode','lesson','preference','fact')", name="memories_kind_check"
        ),
        CheckConstraint("importance BETWEEN 0 AND 1", name="memories_importance_check"),
        Index(
            "memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        Index("memories_tsv_gin", "tsv", postgresql_using="gin"),
        Index(
            "memories_ns_kind",
            "namespace",
            "kind",
            postgresql_where=sa_text("consolidated_into IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    namespace: Mapped[str] = mapped_column(Text, nullable=False)  # 'user:<id>' or 'team:<id>'
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Null until embedded: writes made while the embedding endpoint (VPN) is down are backfilled.
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIM))
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, text)", persisted=True)
    )
    importance: Mapped[float] = mapped_column(REAL, nullable=False)
    source_run_id: Mapped[uuid.UUID | None] = _fk("runs.id", nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consolidated_into: Mapped[uuid.UUID | None] = _fk(
        "memories.id", nullable=True, ondelete="SET NULL"
    )
    derived_from: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=sa_text("'{}'")
    )
    created_at: Mapped[datetime] = _created_at()


# Artifacts, events, audit, tool registry


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID | None] = _fk("runs.id", nullable=True)
    task_id: Mapped[uuid.UUID | None] = _fk("tasks.id", nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class RunEvent(Base):
    """Transactional outbox for SSE and the audit of agent decisions."""

    __tablename__ = "run_events"
    __table_args__ = (Index("run_events_run_idx", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = _fk("runs.id")
    type: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    """Append-only: the app role has no UPDATE or DELETE on this table."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)  # null = system
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class AppMeta(Base):
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)


class ToolRow(Base):
    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. 'web.search'
    server: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    allowed_specialists: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    rate_limit_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("60")
    )
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmailOutbox(Base):
    """Simulated email written by comms.send_email; never sent (improvement 4)."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint(
            "cardinality(to_addrs) BETWEEN 1 AND 10", name="email_outbox_to_addrs_check"
        ),
        Index("email_outbox_run_idx", "run_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID | None] = _fk("runs.id", nullable=True)
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_calls.id"), unique=True
    )
    to_addrs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("'simulated'"))
    created_at: Mapped[datetime] = _created_at()


class ModelDefault(Base):
    """Admin-set default gateway model per role (amendment A2/A3)."""

    __tablename__ = "model_defaults"
    __table_args__ = (
        CheckConstraint(
            "role IN ('planner','specialist','reviewer','fast')", name="model_defaults_role_check"
        ),
    )

    role: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    updated_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


TABLES_WITH_UPDATED_AT = ("tasks", "tools", "model_defaults")
