"""Costs (SPEC 17.1, FR-62, A4): aggregates from llm_calls and tool_calls for the Costs page."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select

from orchestrix_api.deps import CurrentUser, Resources, require_role
from orchestrix_core.db.models import LlmCall, PlanRow, Run, Task, Team, ToolCall

router = APIRouter(prefix="/v1/costs", tags=["costs"])

GroupBy = Literal["day", "agent", "model", "team", "task_type"]
Operator = Annotated[CurrentUser, require_role("operator")]


class CostRow(BaseModel):
    key: str
    runs: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    notional: bool  # priced from the notional table (self-hosted models, improvement 8)


class CostReport(BaseModel):
    group_by: GroupBy
    since: datetime
    rows: list[CostRow]
    tool_calls: int
    total_usd: float


@router.get("")
async def costs(
    res: Resources,
    user: Operator,
    group_by: GroupBy = "day",
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> CostReport:
    since = datetime.now(UTC) - timedelta(days=days)
    latest_plan = (
        select(PlanRow.run_id, func.max(PlanRow.version).label("v"))
        .group_by(PlanRow.run_id)
        .subquery()
    )
    task_type = func.coalesce(PlanRow.body["deliverable_format"].astext, "unplanned")
    key: Any = {
        "day": cast(LlmCall.created_at, Date),
        "agent": LlmCall.agent,
        "model": LlmCall.model_id,
        "team": Team.name,
        "task_type": task_type,
    }[group_by]
    q = (
        select(
            key.label("key"),
            func.count(func.distinct(LlmCall.run_id)),
            func.count(),
            func.coalesce(func.sum(LlmCall.input_tokens), 0),
            func.coalesce(func.sum(LlmCall.output_tokens), 0),
            func.coalesce(func.sum(LlmCall.cost_usd), 0),
            func.array_agg(func.distinct(LlmCall.model_id)),
        )
        .select_from(LlmCall)
        .join(Run, Run.id == LlmCall.run_id)
        .join(Task, Task.id == Run.task_id)
        .join(Team, Team.id == Task.team_id)
        .outerjoin(latest_plan, latest_plan.c.run_id == Run.id)
        .outerjoin(PlanRow, (PlanRow.run_id == Run.id) & (PlanRow.version == latest_plan.c.v))
        .where(LlmCall.created_at >= since)
        .group_by(key)
        .order_by(key)
    )
    priced = set(res.settings.model_prices)
    async with res.sessions() as s:
        rows = (await s.execute(q)).all()
        tool_count = (
            await s.execute(
                select(func.count()).select_from(ToolCall).where(ToolCall.created_at >= since)
            )
        ).scalar_one()
    out = [
        CostRow(
            key=str(r[0]),
            runs=int(r[1]),
            llm_calls=int(r[2]),
            input_tokens=int(r[3]),
            output_tokens=int(r[4]),
            cost_usd=round(float(r[5]), 6),
            notional=all(m in priced for m in (r[6] or [])),
        )
        for r in rows
    ]
    return CostReport(
        group_by=group_by,
        since=since,
        rows=out,
        tool_calls=int(tool_count),
        total_usd=round(sum(r.cost_usd for r in out), 6),
    )
