"""Production Services for the graph: SQL recorder writing app tables idempotently (SPEC 11.6)."""

import base64
import hashlib
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrix_agents.context import StoredToolCall
from orchestrix_core import metrics
from orchestrix_core.db.models import (
    Artifact,
    EmailOutbox,
    LlmCall,
    ModelDefault,
    PlanRow,
    ReviewRow,
    Run,
    RunEvent,
    SubtaskRow,
    Task,
    ToolCall,
    ToolRow,
)
from orchestrix_core.db.repos.audit import AuditRepo
from orchestrix_core.db.repos.tasks import TaskRepo
from orchestrix_core.logging import get_logger
from orchestrix_core.schemas import (
    LlmCallRecord,
    Plan,
    PlannedSubtask,
    PolicyDecision,
    Review,
    SubtaskResult,
    TaskStatus,
)
from orchestrix_core.storage import ObjectStore, artifact_key
from orchestrix_core.telemetry import span
from orchestrix_policy.client import PolicyClient, ToolPolicyInput, tools_document
from orchestrix_tools.catalogue import CATALOGUE

log = get_logger(__name__)

# Deterministic ids make re-run inserts collide instead of duplicating rows.
_NS = uuid.UUID("6f1b8c1e-4d6a-4f0e-9a51-0b7c3e2d9a10")


def stable_id(*parts: object) -> UUID:
    return uuid.uuid5(_NS, ":".join(str(p) for p in parts))


def _uuids(values: list[str]) -> list[UUID]:
    out: list[UUID] = []
    for v in values:
        try:
            out.append(UUID(v))
        except ValueError:
            continue  # planner cited something that is not a memory id
    return out


class SqlRunRecorder:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tasks: TaskRepo,
        store: ObjectStore,
        *,
        replay: bool = False,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._store = store
        self._replay = replay  # replays write their own rows but never touch the task

    def for_replay(self) -> "SqlRunRecorder":
        return SqlRunRecorder(self._sessions, self._tasks, self._store, replay=True)

    async def set_task_status(
        self, task_id: str, status: TaskStatus, reason: str | None = None
    ) -> None:
        if self._replay:
            return
        await self._tasks.set_status(UUID(task_id), status, reason)

    async def save_plan(self, run_id: str, version: int, plan: Plan, edited_by: str | None) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                insert(PlanRow)
                .values(
                    run_id=UUID(run_id),
                    version=version,
                    body=plan.model_dump(mode="json"),
                    confidence=plan.confidence,
                    rationale=plan.confidence_reason,
                    memory_ids=_uuids(plan.used_memory_ids),
                    edited_by=next(iter(_uuids([edited_by] if edited_by else [])), None),
                )
                .on_conflict_do_nothing(index_elements=["run_id", "version"])
            )

    async def save_subtask(
        self,
        run_id: str,
        plan_version: int,
        subtask: PlannedSubtask,
        status: str,
        *,
        attempts: int = 0,
        revisions: int = 0,
        result: SubtaskResult | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "attempts": attempts,
            "revisions": revisions,
        }
        if result is not None:
            values["result"] = result.model_dump(mode="json")
        async with self._sessions.begin() as s:
            await s.execute(
                insert(SubtaskRow)
                .values(
                    run_id=UUID(run_id),
                    plan_version=plan_version,
                    key=subtask.key,
                    specialist=subtask.specialist,
                    **values,
                )
                .on_conflict_do_update(
                    index_elements=["run_id", "plan_version", "key"], set_=values
                )
            )

    async def save_review(
        self, run_id: str, subtask_key: str | None, ordinal: int, review: Review, model: str
    ) -> None:
        async with self._sessions.begin() as s:
            subtask_id = None
            if subtask_key is not None:
                subtask_id = (
                    await s.execute(
                        select(SubtaskRow.id)
                        .where(SubtaskRow.run_id == UUID(run_id), SubtaskRow.key == subtask_key)
                        .order_by(SubtaskRow.plan_version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            await s.execute(
                insert(ReviewRow)
                .values(
                    id=stable_id(run_id, "review", subtask_key or "final", ordinal),
                    run_id=UUID(run_id),
                    subtask_id=subtask_id,
                    verdict=review.verdict,
                    score=review.score,
                    issues=[i.model_dump(mode="json") for i in review.issues],
                    feedback=review.feedback,
                    reviewer_model=model,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )

    async def save_deliverable(self, run_id: str, task_id: str, content: str) -> str | None:
        body = content.encode()
        key = f"runs/{run_id}/deliverable.md"
        await self._store.put(key, body, "text/markdown")
        artifact_id = stable_id(run_id, "deliverable")
        async with self._sessions.begin() as s:
            await s.execute(
                insert(Artifact)
                .values(
                    id=artifact_id,
                    run_id=UUID(run_id),
                    task_id=UUID(task_id),
                    name="deliverable.md",
                    mime="text/markdown",
                    bytes=len(body),
                    object_key=key,
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            if not self._replay:
                await s.execute(
                    update(Task)
                    .where(Task.id == UUID(task_id))
                    .values(final_artifact_id=artifact_id)
                )
        return str(artifact_id)


PREVIEW_BYTES = 32 * 1024  # FR-23: first 32 KB inline, full payload in object storage


class SqlToolCallStore:
    """tool_calls rows keyed by idempotency key (FR-23, plan improvement 3)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: ObjectStore) -> None:
        self._sessions = sessions
        self._store = store

    async def find(self, idempotency_key: str) -> StoredToolCall | None:
        async with self._sessions() as s:
            row = (
                await s.execute(select(ToolCall).where(ToolCall.idempotency_key == idempotency_key))
            ).scalar_one_or_none()
        if row is None:
            return None
        output = row.output_preview or ""
        if row.output_object_key:
            output = (await self._store.get(row.output_object_key)).decode(errors="replace")
        return StoredToolCall(id=str(row.id), output=output, status=row.status)

    async def record(
        self,
        *,
        run_id: str,
        subtask_key: str | None,
        idempotency_key: str,
        tool: str,
        args: dict[str, Any],
        output: str,
        status: str,
        latency_ms: int,
        error: str | None,
        policy_decision: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> str:
        body = output.encode()
        object_key = None
        if len(body) > PREVIEW_BYTES:
            object_key = f"tool-outputs/{run_id}/{idempotency_key}"
            await self._store.put(object_key, body, "text/plain; charset=utf-8")
        async with self._sessions.begin() as s:
            subtask_id = None
            if subtask_key is not None:
                subtask_id = (
                    await s.execute(
                        select(SubtaskRow.id)
                        .where(SubtaskRow.run_id == UUID(run_id), SubtaskRow.key == subtask_key)
                        .order_by(SubtaskRow.plan_version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            inserted = (
                await s.execute(
                    insert(ToolCall)
                    .values(
                        run_id=UUID(run_id),
                        subtask_id=subtask_id,
                        idempotency_key=idempotency_key,
                        tool=tool,
                        server=tool.split(".", 1)[0],
                        args=args,
                        output_preview=body[:PREVIEW_BYTES].decode(errors="ignore"),
                        output_object_key=object_key,
                        status=status,
                        latency_ms=latency_ms,
                        error=error,
                        policy_decision=policy_decision,
                        trace_id=trace_id,
                        span_id=span_id,
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(ToolCall.id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                inserted = (
                    await s.execute(
                        select(ToolCall.id).where(ToolCall.idempotency_key == idempotency_key)
                    )
                ).scalar_one()
        return str(inserted)


class SqlArtifactBridge:
    """Artifact I/O for the sandbox (orchestrix_tools.client.SandboxFiles): run-scoped reads,
    deterministic ids for created files so a replayed call rewrites the same artifacts."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: ObjectStore) -> None:
        self._sessions = sessions
        self._store = store

    async def read_for_run(self, run_id: str, artifact_ids: list[str]) -> list[tuple[str, bytes]]:
        wanted: list[UUID] = []
        for raw in artifact_ids:
            try:
                wanted.append(UUID(raw))
            except ValueError as exc:
                raise LookupError(f"no artifact {raw} for this run") from exc
        if not wanted:
            return []
        async with self._sessions() as s:
            task_id = (
                await s.execute(select(Run.task_id).where(Run.id == UUID(run_id)))
            ).scalar_one()
            rows = {
                a.id: a
                for a in (
                    await s.execute(
                        select(Artifact).where(
                            Artifact.id.in_(wanted),
                            or_(Artifact.run_id == UUID(run_id), Artifact.task_id == task_id),
                        )
                    )
                ).scalars()
            }
        out: list[tuple[str, bytes]] = []
        for artifact_id in wanted:
            artifact = rows.get(artifact_id)
            if artifact is None:  # unknown, or another task's: same answer
                raise LookupError(f"no artifact {artifact_id} for this run")
            out.append((artifact.name, await self._store.get(artifact.object_key)))
        return out

    async def store_created(
        self, run_id: str, call_key: str, files: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        async with self._sessions.begin() as s:
            task_id = (
                await s.execute(select(Run.task_id).where(Run.id == UUID(run_id)))
            ).scalar_one()
            for f in files:
                body = base64.b64decode(str(f["content_b64"]))
                name = str(f["name"])
                mime = str(f.get("mime") or "application/octet-stream")
                artifact_id = stable_id(call_key, "sandbox-file", name)
                key = artifact_key(str(artifact_id))
                await self._store.put(key, body, mime)
                await s.execute(
                    insert(Artifact)
                    .values(
                        id=artifact_id,
                        run_id=UUID(run_id),
                        task_id=task_id,
                        name=name,
                        mime=mime,
                        bytes=len(body),
                        object_key=key,
                        sha256=hashlib.sha256(body).hexdigest(),
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                stored.append(
                    {
                        "artifact_id": str(artifact_id),
                        "name": name,
                        "mime": mime,
                        "bytes": len(body),
                    }
                )
        return stored


async def sync_tool_registry(
    sessions: async_sessionmaker[AsyncSession], loaded: dict[str, Any]
) -> None:
    """SPEC 12.4: the tools table mirrors the servers' schemas; admin-owned flags are never reset."""
    async with sessions.begin() as s:
        for name, spec in CATALOGUE.items():
            tool = loaded.get(name)
            if tool is None:
                continue
            schema: dict[str, Any] = (
                tool.args_schema
                if isinstance(tool.args_schema, dict)
                else tool.args_schema.model_json_schema()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            )
            await s.execute(
                insert(ToolRow)
                .values(
                    name=name,
                    server=spec.server,
                    sensitive=spec.sensitive,
                    allowed_specialists=list(spec.allowed_specialists),
                    rate_limit_per_min=spec.rate_limit_per_min,
                    input_schema=schema,
                )
                .on_conflict_do_update(
                    index_elements=["name"],
                    set_={"server": spec.server, "input_schema": schema},  # schema only; flags stay
                )
            )


async def enabled_tools(sessions: async_sessionmaker[AsyncSession]) -> set[str]:
    async with sessions() as s:
        return set(
            (await s.execute(select(ToolRow.name).where(ToolRow.enabled.is_(True)))).scalars()
        )


class SqlPolicyGate:
    """PolicyGate (FR-90): OPA decides; the rate input is counted from tool_calls; denials are
    audited. OPA keeps registry data in memory, so an 'unknown tool' for a catalogued tool
    (OPA restarted) re-pushes the registry once and asks again."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: PolicyClient,
        audit: AuditRepo,
        org_email_domain: str,
    ) -> None:
        self._sessions = sessions
        self._client = client
        self._audit = audit
        self._domain = org_email_domain

    async def push_registry(self) -> None:
        async with self._sessions() as s:
            rows = list((await s.execute(select(ToolRow))).scalars())
        await self._client.push("tools", tools_document(rows))
        side_effects = sorted(n for n, spec in CATALOGUE.items() if spec.side_effecting)
        await self._client.push(
            "org", {"email_domain": self._domain, "side_effect_tools": side_effects}
        )

    async def _calls_last_minute(self, tool: str) -> int:
        async with self._sessions() as s:
            return int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(ToolCall)
                        .where(
                            ToolCall.tool == tool,
                            ToolCall.created_at > text("now() - interval '1 minute'"),
                        )
                    )
                ).scalar_one()
            )

    async def check_tool(
        self,
        *,
        run_id: str,
        tool: str,
        specialist: str,
        args: dict[str, Any],
        user_id: str,
        team_id: str,
        tainted: bool,
    ) -> PolicyDecision:
        data = ToolPolicyInput(
            tool=tool,
            specialist=specialist,
            args=args,
            user=user_id,
            team=team_id,
            calls_last_minute=await self._calls_last_minute(tool),
            context_tainted=tainted,
        )
        with span("policy.check", **{"orchestrix.tool": tool, "orchestrix.run_id": run_id}) as sp:
            decision = await self._client.tool_decision(data)
            if "unknown tool" in decision.reasons and tool in CATALOGUE:
                try:
                    await self.push_registry()
                    decision = await self._client.tool_decision(data)
                except Exception:  # still unknown: stays denied (fail closed)
                    log.warning("could not re-push the tool registry to OPA", tool=tool)
            if sp is not None:
                sp.set_attribute("orchestrix.policy.result", decision.result)
        metrics.POLICY_DECISIONS.labels(decision.result, tool).inc()
        return decision

    async def record_denial(
        self, *, run_id: str, tool: str, args: dict[str, Any], reasons: list[str]
    ) -> None:
        await self._audit.write(
            actor_id=None,
            action="policy.deny",
            target_type="tool_call",
            target_id=f"{run_id}:{tool}",
            after={"tool": tool, "arg_keys": sorted(args)},  # no argument values in the log
            reason="; ".join(reasons),
        )


class SqlLlmCallSink:
    """llm_calls rows (SPEC 15.3, A6)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, call: LlmCallRecord) -> None:
        async with self._sessions.begin() as s:
            s.add(
                LlmCall(
                    run_id=UUID(call.run_id),
                    agent=call.agent,
                    model_alias=call.model_alias,
                    model_id=call.model_id,
                    requested_model=call.requested_model,
                    purpose=call.purpose,
                    subtask_key=call.subtask_key,
                    attempt=call.attempt,
                    revision=call.revision,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cost_usd=Decimal(str(round(call.cost_usd, 6))),
                    latency_ms=call.latency_ms,
                    trace_id=call.trace_id,
                    span_id=call.span_id,
                )
            )


async def admin_model_defaults(sessions: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """A2/A4: the admin default per role (model_defaults table)."""
    async with sessions() as s:
        rows = (await s.execute(select(ModelDefault.role, ModelDefault.model))).all()
    return {role: model for role, model in rows}


async def apply_retention(
    sessions: async_sessionmaker[AsyncSession],
    store: ObjectStore,
    checkpointer: Any,
    *,
    trace_days: int,
    run_days: int,
) -> dict[str, int]:
    """NFR-06 / SPEC 10 data rules. Trace data (events, llm and tool calls, payloads) after
    `trace_days`; checkpoints of runs that ended more than `run_days` ago. The audit log is
    append-only for the app role, so its 1-year window is the migrator's job (BUILD_LOG)."""
    cutoff = text(f"now() - interval '{int(trace_days)} days'")
    async with sessions.begin() as s:
        keys = list(
            (
                await s.execute(
                    select(ToolCall.output_object_key).where(
                        ToolCall.created_at < cutoff, ToolCall.output_object_key.is_not(None)
                    )
                )
            ).scalars()
        )
        events = await s.execute(delete(RunEvent).where(RunEvent.created_at < cutoff))
        llm = await s.execute(delete(LlmCall).where(LlmCall.created_at < cutoff))
        # email_outbox rows reference tool calls; keep the outbox, drop the link
        await s.execute(
            update(EmailOutbox)
            .where(
                EmailOutbox.tool_call_id.in_(
                    select(ToolCall.id).where(ToolCall.created_at < cutoff)
                )
            )
            .values(tool_call_id=None)
        )
        tools = await s.execute(delete(ToolCall).where(ToolCall.created_at < cutoff))
        threads = list(
            (
                await s.execute(
                    select(Run.thread_id)
                    .where(Run.ended_at < text(f"now() - interval '{int(run_days)} days'"))
                    .distinct()
                )
            ).scalars()
        )
    for key in keys:
        if key:
            await store.delete(key)
    for thread in threads:
        await checkpointer.adelete_thread(thread)
    return {
        "run_events": events.rowcount or 0,  # pyright: ignore[reportAttributeAccessIssue]
        "llm_calls": llm.rowcount or 0,  # pyright: ignore[reportAttributeAccessIssue]
        "tool_calls": tools.rowcount or 0,  # pyright: ignore[reportAttributeAccessIssue]
        "payloads": len(keys),
        "threads": len(threads),
    }
