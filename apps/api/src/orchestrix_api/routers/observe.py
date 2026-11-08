"""Run views for the UI (SPEC 17.1, 19.3; FR-61, FR-62; amendment A6): plan graph, trace tree,
node payloads, efficiency breakdown. Built from Orchestrix's own tables, so the product UI
never depends on Langfuse; the trace links out to it when tracing is configured."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from orchestrix_api.deps import Resources, User, require_task_access
from orchestrix_core.db.models import (
    Escalation,
    EscalationAction,
    LlmCall,
    PlanRow,
    ReviewRow,
    Run,
    RunEvent,
    SubtaskRow,
    ToolCall,
)
from orchestrix_core.telemetry import run_trace_id

router = APIRouter(prefix="/v1/runs", tags=["runs"])
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

NodeKind = Literal[
    "run",
    "plan",
    "memory",
    "subtask",
    "llm_call",
    "tool_call",
    "review",
    "escalation",
    "decision",
    "synthesis",
    "final_review",
    "memory_write",
]


async def _run(res: Resources, user: Any, run_id: UUID) -> Run:
    run = await res.runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    await require_task_access(res, user, run.task_id)
    return run


# --- plan graph ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    key: str
    specialist: str
    description: str
    status: str
    attempts: int
    revisions: int
    expected_output: str


class GraphEdge(BaseModel):
    source: str
    target: str


class RunGraphView(BaseModel):
    run_id: UUID
    plan_version: int | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@router.get("/{run_id}/graph")
async def run_graph(run_id: UUID, res: Resources, user: User) -> RunGraphView:
    await _run(res, user, run_id)
    plan = await res.runs.latest_plan(run_id)
    if plan is None:
        return RunGraphView(run_id=run_id, plan_version=None, nodes=[], edges=[])
    rows = {s.key: s for s in await res.runs.subtasks(run_id, plan.version)}
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for sub in plan.body.get("subtasks", []):
        row = rows.get(sub["key"])
        nodes.append(
            GraphNode(
                key=sub["key"],
                specialist=sub["specialist"],
                description=sub["description"],
                status=row.status if row else "pending",
                attempts=row.attempts if row else 0,
                revisions=row.revisions if row else 0,
                expected_output=sub.get("expected_output", "markdown"),
            )
        )
        edges += [GraphEdge(source=d, target=sub["key"]) for d in sub.get("depends_on", [])]
    return RunGraphView(run_id=run_id, plan_version=plan.version, nodes=nodes, edges=edges)


# --- subtask detail -----------------------------------------------------------------------


class ReviewView(BaseModel):
    verdict: str
    score: float
    issues: list[dict[str, Any]]
    feedback: str
    reviewer_model: str
    created_at: datetime


class SubtaskDetail(BaseModel):
    key: str
    specialist: str
    status: str
    attempts: int
    revisions: int
    plan_version: int
    result: dict[str, Any] | None  # SubtaskResult: output, sources, artifacts, confidence, notes
    reviews: list[ReviewView]


@router.get("/{run_id}/subtasks/{key}")
async def subtask_detail(run_id: UUID, key: str, res: Resources, user: User) -> SubtaskDetail:
    """The subtask drawer: result, sources and every review (SPEC 18.1 task detail)."""
    await _run(res, user, run_id)
    async with res.sessions() as s:
        row = (
            await s.execute(
                select(SubtaskRow)
                .where(SubtaskRow.run_id == run_id, SubtaskRow.key == key)
                .order_by(SubtaskRow.plan_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "subtask not found")
        reviews = list(
            (
                await s.execute(
                    select(ReviewRow)
                    .where(ReviewRow.subtask_id == row.id)
                    .order_by(ReviewRow.created_at)
                )
            ).scalars()
        )
    return SubtaskDetail(
        key=row.key,
        specialist=row.specialist,
        status=row.status,
        attempts=row.attempts,
        revisions=row.revisions,
        plan_version=row.plan_version,
        result=row.result,
        reviews=[
            ReviewView(
                verdict=r.verdict,
                score=r.score,
                issues=list(r.issues),
                feedback=r.feedback,
                reviewer_model=r.reviewer_model,
                created_at=r.created_at,
            )
            for r in reviews
        ],
    )


# --- trace tree (19.3) --------------------------------------------------------------------


class TraceNode(BaseModel):
    id: str  # "<kind>:<row id>", the handle for GET .../trace/{kind}/{item_id}
    kind: NodeKind
    label: str
    status: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int | None = None
    payload: dict[str, Any] = {}  # small inline facts; large payloads load lazily
    children: list["TraceNode"] = []


class TraceView(BaseModel):
    run_id: UUID
    langfuse_url: str | None
    root: TraceNode


def _llm_node(c: LlmCall) -> TraceNode:
    fell_back = bool(c.requested_model and c.requested_model != c.model_id)
    return TraceNode(
        id=f"llm_call:{c.id}",
        kind="llm_call",
        label=f"{c.agent} · {c.purpose or c.model_alias} · {c.model_id}",
        status="fallback" if fell_back else "ok",
        started_at=c.created_at,
        input_tokens=c.input_tokens,
        output_tokens=c.output_tokens,
        cost_usd=float(c.cost_usd),
        latency_ms=c.latency_ms,
        payload={
            "model_alias": c.model_alias,
            "requested_model": c.requested_model,
            "model_id": c.model_id,
            "attempt": c.attempt,
            "revision": c.revision,
            "langfuse_span_id": c.span_id,
        },
    )


def _tool_node(t: ToolCall) -> TraceNode:
    return TraceNode(
        id=f"tool_call:{t.id}",
        kind="tool_call",
        label=t.tool,
        status=t.status,
        started_at=t.created_at,
        latency_ms=t.latency_ms,
        payload={"args": t.args, "error": t.error, "has_full_output": bool(t.output_object_key)},
    )


def _review_node(r: ReviewRow, kind: NodeKind = "review") -> TraceNode:
    return TraceNode(
        id=f"review:{r.id}",
        kind=kind,
        label=f"{r.verdict} · {r.score:g}/10",
        status=r.verdict,
        started_at=r.created_at,
        payload={"issues": r.issues, "feedback": r.feedback, "reviewer_model": r.reviewer_model},
    )


def _sum(node: TraceNode) -> TraceNode:
    """Roll tokens and cost up the tree."""
    for child in node.children:
        _sum(child)
        node.input_tokens += child.input_tokens
        node.output_tokens += child.output_tokens
        node.cost_usd = round(node.cost_usd + child.cost_usd, 6)
    return node


@router.get("/{run_id}/trace")
async def run_trace(run_id: UUID, res: Resources, user: User) -> TraceView:
    run = await _run(res, user, run_id)
    async with res.sessions() as s:
        plans = list(
            (
                await s.execute(
                    select(PlanRow).where(PlanRow.run_id == run_id).order_by(PlanRow.version)
                )
            ).scalars()
        )
        subtasks = list(
            (
                await s.execute(
                    select(SubtaskRow)
                    .where(SubtaskRow.run_id == run_id)
                    .order_by(SubtaskRow.plan_version, SubtaskRow.key)
                )
            ).scalars()
        )
        calls = list(
            (
                await s.execute(
                    select(LlmCall).where(LlmCall.run_id == run_id).order_by(LlmCall.created_at)
                )
            ).scalars()
        )
        tools = list(
            (
                await s.execute(
                    select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at)
                )
            ).scalars()
        )
        reviews = list(
            (
                await s.execute(
                    select(ReviewRow)
                    .where(ReviewRow.run_id == run_id)
                    .order_by(ReviewRow.created_at)
                )
            ).scalars()
        )
        escalations = list(
            (
                await s.execute(
                    select(Escalation)
                    .where(Escalation.run_id == run_id)
                    .order_by(Escalation.created_at)
                )
            ).scalars()
        )
        actions = list(
            (
                await s.execute(
                    select(EscalationAction)
                    .join(Escalation, Escalation.id == EscalationAction.escalation_id)
                    .where(Escalation.run_id == run_id)
                )
            ).scalars()
        )
        memory_events = list(
            (
                await s.execute(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.type.in_(["memory.retrieved", "memory.written"]),
                    )
                    .order_by(RunEvent.id)
                )
            ).scalars()
        )

    root = TraceNode(
        id=f"run:{run.id}",
        kind="run",
        label=f"run · {run.kind}",
        status=run.status,
        started_at=run.started_at,
        ended_at=run.ended_at,
        payload={"graph_version": run.graph_version, "models": run.model_config_ or {}},
    )
    for ev in memory_events:
        if ev.type == "memory.retrieved":
            root.children.append(
                TraceNode(
                    id=f"memory:{ev.id}",
                    kind="memory",
                    label="memory retrieved",
                    started_at=ev.created_at,
                    payload=ev.data,
                )
            )
    for p in plans:
        plan_node = TraceNode(
            id=f"plan:{p.id}",
            kind="plan",
            label=f"plan v{p.version} · confidence {p.confidence:.2f}",
            started_at=p.created_at,
            payload={
                "rationale": p.rationale,
                "memory_ids": [str(m) for m in p.memory_ids],
                "edited_by": str(p.edited_by) if p.edited_by else None,
            },
        )
        plan_node.children = (
            [
                _llm_node(c)
                for c in calls
                if c.purpose in ("plan", "plan_repair") and c.subtask_key is None
            ]
            if p is plans[0]
            else []
        )
        root.children.append(plan_node)

    by_subtask_id = {s.id: s.key for s in subtasks}
    latest: dict[str, SubtaskRow] = {}
    for sub in subtasks:
        latest[sub.key] = sub
    for key, sub in sorted(latest.items()):
        node = TraceNode(
            id=f"subtask:{sub.id}",
            kind="subtask",
            label=f"{key} · {sub.specialist}",
            status=sub.status,
            payload={"attempts": sub.attempts, "revisions": sub.revisions},
        )
        items: list[TraceNode] = [_llm_node(c) for c in calls if c.subtask_key == key]
        items += [
            _tool_node(t) for t in tools if t.subtask_id and by_subtask_id.get(t.subtask_id) == key
        ]
        items += [
            _review_node(r)
            for r in reviews
            if r.subtask_id and by_subtask_id.get(r.subtask_id) == key
        ]
        items += [
            _escalation_node(e, actions) for e in escalations if e.payload.get("subtask") == key
        ]
        node.children = sorted(items, key=lambda n: n.started_at or EPOCH)
        root.children.append(node)

    synth = [c for c in calls if c.purpose == "synthesis"]
    if synth:
        root.children.append(
            TraceNode(
                id=f"synthesis:{run.id}",
                kind="synthesis",
                label="synthesis",
                children=[_llm_node(c) for c in synth],
            )
        )
    finals = [r for r in reviews if r.subtask_id is None]
    if finals:
        final_calls = [_llm_node(c) for c in calls if c.purpose == "final_review"]
        root.children.append(
            TraceNode(
                id=f"final_review:{run.id}",
                kind="final_review",
                label="final review",
                status=finals[-1].verdict,
                children=[_review_node(r, "review") for r in finals] + final_calls,
            )
        )
    root.children += [
        _escalation_node(e, actions) for e in escalations if not e.payload.get("subtask")
    ]
    for ev in memory_events:
        if ev.type == "memory.written":
            root.children.append(
                TraceNode(
                    id=f"memory_write:{ev.id}",
                    kind="memory_write",
                    label="memory written",
                    started_at=ev.created_at,
                    payload=ev.data,
                )
            )

    # Every call appears exactly once (SPEC 1: a complete cost record): anything not placed
    # above (memory, chat, or a subtask with no row yet) hangs off the run itself.
    def placed(node: TraceNode) -> set[str]:
        ids: set[str] = {node.id} if node.kind == "llm_call" else set()
        for child in node.children:
            ids |= placed(child)
        return ids

    shown = placed(root)
    root.children += [_llm_node(c) for c in calls if f"llm_call:{c.id}" not in shown]

    host = res.settings.langfuse_host
    url = f"{host.rstrip('/')}/trace/{run_trace_id(str(run.id))}" if host else None
    return TraceView(run_id=run_id, langfuse_url=url, root=_sum(root))


def _escalation_node(e: Escalation, actions: list[EscalationAction]) -> TraceNode:
    return TraceNode(
        id=f"escalation:{e.id}",
        kind="escalation",
        label=f"{e.trigger} · {e.level}",
        status=e.status,
        started_at=e.created_at,
        payload={"title": e.payload.get("title"), "proposed": e.payload.get("proposed")},
        children=[
            TraceNode(
                id=f"decision:{a.id}",
                kind="decision",
                label=f"{a.action} by {a.actor_id}",
                status=a.action,
                started_at=a.created_at,
                payload={k: v for k, v in a.body.items() if k != "output"},
            )
            for a in actions
            if a.escalation_id == e.id
        ],
    )


class NodePayload(BaseModel):
    kind: str
    id: UUID
    data: dict[str, Any]


@router.get("/{run_id}/trace/{kind}/{item_id}")
async def trace_payload(
    run_id: UUID,
    kind: Literal["tool_call", "llm_call", "review", "subtask"],
    item_id: UUID,
    res: Resources,
    user: User,
) -> NodePayload:
    """Full payload of one trace node, loaded lazily (19.3)."""
    await _run(res, user, run_id)
    async with res.sessions() as s:
        model: Any = {
            "tool_call": ToolCall,
            "llm_call": LlmCall,
            "review": ReviewRow,
            "subtask": SubtaskRow,
        }[kind]
        row: Any = await s.get(model, item_id)
    if row is None or row.run_id != run_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trace node not found")
    data: dict[str, Any] = {a.key: getattr(row, a.key) for a in sa_inspect(model).column_attrs}
    if kind == "tool_call" and row.output_object_key:
        data["output"] = (await res.objects.get(row.output_object_key)).decode(errors="replace")
    clean = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in data.items()}
    return NodePayload(kind=kind, id=item_id, data=clean)


# --- efficiency (A6) ----------------------------------------------------------------------


class Bucket(BaseModel):
    key: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Efficiency(BaseModel):
    run_id: UUID
    total: Bucket
    by_subtask: list[Bucket]
    by_purpose: list[Bucket]
    by_model: list[Bucket]
    revisions: Bucket  # spent on reviewer-requested rework
    retries: Bucket  # spent on failed attempts and alternative approaches
    fallbacks: Bucket  # served by a different model than requested
    notional: bool  # every cost came from the notional price table (improvement 8)


def _add(b: Bucket, c: LlmCall) -> None:
    b.calls += 1
    b.input_tokens += c.input_tokens
    b.output_tokens += c.output_tokens
    b.cost_usd = round(b.cost_usd + float(c.cost_usd), 6)


@router.get("/{run_id}/efficiency")
async def efficiency(run_id: UUID, res: Resources, user: User) -> Efficiency:
    await _run(res, user, run_id)
    async with res.sessions() as s:
        calls = list((await s.execute(select(LlmCall).where(LlmCall.run_id == run_id))).scalars())
    total, revisions, retries, fallbacks = (
        Bucket(key=k) for k in ("total", "revisions", "retries", "fallbacks")
    )
    groups: dict[str, dict[str, Bucket]] = {"subtask": {}, "purpose": {}, "model": {}}
    for c in calls:
        _add(total, c)
        for dim, key in (
            ("subtask", c.subtask_key or "(run)"),
            ("purpose", c.purpose or "unknown"),
            ("model", c.model_id),
        ):
            _add(groups[dim].setdefault(key, Bucket(key=key)), c)
        if c.purpose == "revision" or (c.purpose == "review" and c.revision > 0):
            _add(revisions, c)
        if c.purpose == "retry" or c.attempt > 0:
            _add(retries, c)
        if c.requested_model and c.requested_model != c.model_id:
            _add(fallbacks, c)
    notional = all(c.model_id in res.settings.model_prices for c in calls)
    return Efficiency(
        run_id=run_id,
        total=total,
        by_subtask=sorted(groups["subtask"].values(), key=lambda b: b.key),
        by_purpose=sorted(groups["purpose"].values(), key=lambda b: -b.cost_usd),
        by_model=sorted(groups["model"].values(), key=lambda b: -b.cost_usd),
        revisions=revisions,
        retries=retries,
        fallbacks=fallbacks,
        notional=notional,
    )
