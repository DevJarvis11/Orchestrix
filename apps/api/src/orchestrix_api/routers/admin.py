"""Administration (SPEC 17.1, FR-91, FR-92, amendment A4): model defaults, tool registry,
budgets and the audit log. Every change writes an audit row in the same transaction."""

import csv
import io
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from orchestrix_api.deps import CurrentUser, Resources, require_role
from orchestrix_core.db.models import AppMeta, ModelDefault, Team, ToolRow
from orchestrix_core.schemas import Budget, Role, Specialist
from orchestrix_policy.client import tools_document

router = APIRouter(prefix="/v1/admin", tags=["admin"])

Admin = Annotated[CurrentUser, require_role("admin")]
Operator = Annotated[CurrentUser, require_role("operator")]  # admins pass too (SPEC 2)
DEFAULT_BUDGET_KEY = "default_budget"


# --- model defaults (A2/A4) ------------------------------------------------------------------


class ModelDefaultView(BaseModel):
    role: Role
    model: str
    params: dict[str, Any] = {}
    source: Literal["admin", "settings"]
    updated_at: datetime | None = None


class ModelDefaultPut(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = {}


@router.get("/model-defaults")
async def get_model_defaults(res: Resources, user: Admin) -> list[ModelDefaultView]:
    async with res.sessions() as s:
        rows = {r.role: r for r in (await s.execute(select(ModelDefault))).scalars()}
    out: list[ModelDefaultView] = []
    for role, model in res.settings.default_models.items():
        row = rows.get(role)
        out.append(
            ModelDefaultView(
                role=role,
                model=row.model,
                params=row.params,
                source="admin",
                updated_at=row.updated_at,
            )
            if row
            else ModelDefaultView(role=role, model=model, source="settings")
        )
    return out


@router.put("/model-defaults/{role}")
async def put_model_default(
    role: Role, body: ModelDefaultPut, res: Resources, user: Admin
) -> ModelDefaultView:
    async with res.sessions.begin() as s:
        before = await s.get(ModelDefault, role)
        before_doc = {"model": before.model, "params": before.params} if before else None
        await s.execute(
            insert(ModelDefault)
            .values(role=role, model=body.model, params=body.params, updated_by=user.id)
            .on_conflict_do_update(
                index_elements=["role"],
                set_={"model": body.model, "params": body.params, "updated_by": user.id},
            )
        )
        await res.audit.write(
            actor_id=user.id,
            action="model_default.update",
            target_type="model_default",
            target_id=role,
            before=before_doc,
            after=body.model_dump(),
            session=s,
        )
    return ModelDefaultView(role=role, model=body.model, params=body.params, source="admin")


# --- tool registry (FR-91) ------------------------------------------------------------------


class ToolView(BaseModel):
    name: str
    server: str
    enabled: bool
    sensitive: bool
    allowed_specialists: list[str]
    rate_limit_per_min: int
    input_schema: dict[str, Any]
    updated_at: datetime


class ToolPatch(BaseModel):
    enabled: bool | None = None
    sensitive: bool | None = None
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=10_000)
    allowed_specialists: list[Specialist] | None = None


def _plain(value: object) -> object:
    if isinstance(value, list | tuple):
        items: list[object] = list(cast(list[object], value))
        return [str(v) for v in items]
    return value


def _cell(value: object) -> object:
    return value if isinstance(value, str | int | float | bool | None) else str(value)


def _tool(r: ToolRow) -> ToolView:
    return ToolView(
        name=r.name,
        server=r.server,
        enabled=r.enabled,
        sensitive=r.sensitive,
        allowed_specialists=list(r.allowed_specialists),
        rate_limit_per_min=r.rate_limit_per_min,
        input_schema=r.input_schema,
        updated_at=r.updated_at,
    )


@router.get("/tools")
async def list_tools(res: Resources, user: Operator) -> list[ToolView]:
    async with res.sessions() as s:
        rows = (await s.execute(select(ToolRow).order_by(ToolRow.name))).scalars()
        return [_tool(r) for r in rows]


@router.get("/tools/{name}")
async def get_tool(name: str, res: Resources, user: Operator) -> ToolView:
    async with res.sessions() as s:
        row = await s.get(ToolRow, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool not found")
    return _tool(row)


@router.patch("/tools/{name}")
async def patch_tool(name: str, body: ToolPatch, res: Resources, user: Operator) -> ToolView:
    changes = body.model_dump(exclude_none=True)
    async with res.sessions.begin() as s:
        row = await s.get(ToolRow, name, with_for_update=True)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tool not found")
        before: dict[str, Any] = {k: _plain(getattr(row, k)) for k in changes}
        for key, value in changes.items():
            setattr(row, key, _plain(value))
        await res.audit.write(
            actor_id=user.id,
            action="tool.update",
            target_type="tool",
            target_id=name,
            before=before,
            after=changes,
            session=s,
        )
        await s.flush()
        view = _tool(row)
        rows = list((await s.execute(select(ToolRow))).scalars())
        document = tools_document(rows)
    try:  # SPEC 15.1: the registry is pushed to OPA whenever an admin changes it
        await res.policy.push("tools", document)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "saved, but the policy engine could not be updated"
        ) from exc
    return view


# --- budgets (FR-63, FR-91) --------------------------------------------------------------------


class TeamBudget(BaseModel):
    team_id: UUID
    name: str
    monthly_budget_usd: float = Field(ge=0)


class Budgets(BaseModel):
    default_run_budget: Budget
    teams: list[TeamBudget]


class BudgetsPut(BaseModel):
    default_run_budget: Budget | None = None
    teams: dict[UUID, float] = {}  # team id -> monthly USD


async def default_run_budget(res: Any) -> Budget:
    """The admin-set default (app_meta) or the FR-63 settings default."""
    async with res.sessions() as s:
        row = await s.get(AppMeta, DEFAULT_BUDGET_KEY)
    return Budget.model_validate(row.value) if row else res.settings.default_budget


@router.get("/budgets")
async def get_budgets(res: Resources, user: Operator) -> Budgets:
    async with res.sessions() as s:
        teams = list((await s.execute(select(Team).order_by(Team.name))).scalars())
    return Budgets(
        default_run_budget=await default_run_budget(res),
        teams=[
            TeamBudget(team_id=t.id, name=t.name, monthly_budget_usd=float(t.monthly_budget_usd))
            for t in teams
        ],
    )


@router.put("/budgets")
async def put_budgets(body: BudgetsPut, res: Resources, user: Operator) -> Budgets:
    async with res.sessions.begin() as s:
        if body.default_run_budget is not None:
            before = await s.get(AppMeta, DEFAULT_BUDGET_KEY)
            value = body.default_run_budget.model_dump()
            await s.execute(
                insert(AppMeta)
                .values(key=DEFAULT_BUDGET_KEY, value=value)
                .on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )
            await res.audit.write(
                actor_id=user.id,
                action="budget.default.update",
                target_type="budget",
                target_id="default_run",
                before=dict(before.value) if before else None,
                after=value,
                session=s,
            )
        for team_id, usd in body.teams.items():
            team = await s.get(Team, team_id, with_for_update=True)
            if team is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"team {team_id} not found")
            old = float(team.monthly_budget_usd)
            team.monthly_budget_usd = usd  # type: ignore[assignment]
            await res.audit.write(
                actor_id=user.id,
                action="budget.team.update",
                target_type="team",
                target_id=str(team_id),
                before={"monthly_budget_usd": old},
                after={"monthly_budget_usd": usd},
                session=s,
            )
    return await get_budgets(res, user)


# --- audit log (FR-92) ------------------------------------------------------------------------


class AuditEntry(BaseModel):
    id: int
    actor_id: UUID | None
    action: str
    target_type: str
    target_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    created_at: datetime


@router.get("/audit", response_model=list[AuditEntry])
async def audit_log(
    res: Resources,
    user: Admin,
    format: Literal["json", "csv"] = "json",
    limit: Annotated[int, Query(ge=1, le=10_000)] = 200,
    before_id: int | None = None,
    action: str | None = None,
) -> Any:
    rows = await res.audit.list(limit=limit, before_id=before_id, action=action)
    entries = [
        AuditEntry(
            id=r.id,
            actor_id=r.actor_id,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            before=r.before,
            after=r.after,
            reason=r.reason,
            created_at=r.created_at,
        )
        for r in rows
    ]
    if format == "json":
        return entries
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(list(AuditEntry.model_fields))
    for e in entries:
        row: dict[str, Any] = e.model_dump(mode="json")
        cells: list[object] = [_cell(v) for v in row.values()]
        writer.writerow(cells)
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )
