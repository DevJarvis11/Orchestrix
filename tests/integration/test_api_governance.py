"""M4-M8 endpoints against real Postgres, Redis and OPA: escalation decisions (four-eyes),
admin, costs, memories, replay and run views (SPEC 14.3, 15.1, 17.1; FR-43, FR-55, FR-62, FR-91)."""

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest

from orchestrix_core.db.models import AuditLog, LlmCall, Memory, ToolRow
from orchestrix_core.db.repos.escalations import EscalationRepo
from orchestrix_core.db.repos.tasks import UserRepo
from tests.integration.conftest import auth

pytestmark = pytest.mark.integration

TASK = {"title": "Latency report", "description": "Summarise p95 latency."}
PAYLOAD: dict[str, Any] = {
    "trigger": "sensitive_tool",
    "level": "approve_action",
    "title": "Approve comms.send_email",
    "task_summary": "Latency report",
    "plan_progress": [],
    "proposed": {"tool": "comms.send_email", "args": {"to": ["a@orchestrix.local"]}},
    "reasoning": "send the summary",
    "related_memories": [],
    "allowed_actions": ["approve", "approve_with_edits", "reject"],
    "subtask": "s3",
}


def resources(client: httpx.AsyncClient) -> Any:
    return client._transport.app.state.resources  # type: ignore[attr-defined]


async def user(
    client: httpx.AsyncClient, team: str, roles: list[str]
) -> tuple[UUID, dict[str, str]]:
    email = f"{uuid.uuid4().hex[:8]}@test.local"
    users = UserRepo(resources(client).sessions)
    row = await users.upsert(f"dev|{email}", email, email)
    await users.ensure_team_member(row.id, team)
    return row.id, auth(email, roles)


async def task_with_escalation(client: httpx.AsyncClient, team: str) -> dict[str, Any]:
    requester_id, requester = await user(client, team, ["requester"])
    created = (await client.post("/v1/tasks", json=TASK, headers=requester)).json()
    team_id = (await UserRepo(resources(client).sessions).first_team(requester_id)).id  # type: ignore[union-attr]
    escalation_id = await EscalationRepo(resources(client).sessions).upsert_open(
        UUID(created["run_id"]), "interrupt-1", PAYLOAD, assignee_team_id=team_id
    )
    return {**created, "requester": requester, "escalation_id": escalation_id}


# --- escalations (SPEC 14.3, FR-43, four-eyes rule) ----------------------------------------


async def test_fr43_team_reviewer_decides_and_the_run_resumes(client: httpx.AsyncClient) -> None:
    team = f"t-{uuid.uuid4().hex[:6]}"
    t = await task_with_escalation(client, team)
    _, reviewer = await user(client, team, ["requester", "reviewer"])
    url = f"/v1/escalations/{t['escalation_id']}"

    inbox = (await client.get("/v1/escalations", headers=reviewer)).json()
    assert str(t["escalation_id"]) in [e["id"] for e in inbox]
    detail = (await client.get(url, headers=reviewer)).json()
    assert detail["can_decide"] is True

    edits = {"to": ["platform-team@orchestrix.local"]}
    r = await client.post(
        f"{url}/decision", json={"action": "approve_with_edits", "edits": edits}, headers=reviewer
    )
    assert r.status_code == 202, r.text
    assert ("orchestrix.resume_run", t["run_id"]) in client.enqueued  # type: ignore[attr-defined]
    decisions = await EscalationRepo(resources(client).sessions).resolved_decisions(
        UUID(t["run_id"])
    )
    assert decisions["interrupt-1"]["edits"] == edits  # what the worker resumes with

    again = await client.post(f"{url}/decision", json={"action": "approve"}, headers=reviewer)
    assert again.status_code == 409  # already resolved


async def test_four_eyes_requester_cannot_approve_their_own_task(client: httpx.AsyncClient) -> None:
    team = f"t-{uuid.uuid4().hex[:6]}"
    t = await task_with_escalation(client, team)
    url = f"/v1/escalations/{t['escalation_id']}/decision"
    # Even with the reviewer role, the requester is refused by OPA (SPEC 2, 15.1).
    email_headers = t["requester"]
    token = email_headers["Authorization"].removeprefix("Bearer ")
    from orchestrix_api.security.dev_jwt import verify
    from tests.integration.conftest import DEV_SECRET

    claims = verify(DEV_SECRET, token)
    self_reviewer = auth(str(claims["email"]), ["requester", "reviewer"])
    r = await client.post(url, json={"action": "approve"}, headers=self_reviewer)
    assert r.status_code == 403


async def test_other_teams_and_bad_actions_are_refused(client: httpx.AsyncClient) -> None:
    team = f"t-{uuid.uuid4().hex[:6]}"
    t = await task_with_escalation(client, team)
    _, outsider = await user(client, f"other-{uuid.uuid4().hex[:6]}", ["reviewer"])
    url = f"/v1/escalations/{t['escalation_id']}"
    assert (await client.get(url, headers=outsider)).status_code == 404  # no existence leak

    _, reviewer = await user(client, team, ["reviewer"])
    bad = await client.post(
        f"{url}/decision", json={"action": "take_over", "output": "x"}, headers=reviewer
    )
    assert bad.status_code == 422  # not in allowed_actions
    no_reason = await client.post(f"{url}/decision", json={"action": "reject"}, headers=reviewer)
    assert no_reason.status_code == 422  # reject requires a reason


async def test_fr90_decisions_fail_closed_without_opa(client: httpx.AsyncClient) -> None:
    team = f"t-{uuid.uuid4().hex[:6]}"
    t = await task_with_escalation(client, team)
    _, reviewer = await user(client, team, ["reviewer"])
    res = resources(client)
    real = res.policy._base  # pyright: ignore[reportPrivateUsage]
    res.policy._base = "http://127.0.0.1:9"  # pyright: ignore[reportPrivateUsage]  # nothing listens
    try:
        r = await client.post(
            f"/v1/escalations/{t['escalation_id']}/decision",
            json={"action": "approve"},
            headers=reviewer,
        )
    finally:
        res.policy._base = real  # pyright: ignore[reportPrivateUsage]
    assert r.status_code == 403


# --- admin (FR-91, FR-92, A4) ------------------------------------------------------------------


async def test_fr91_admin_endpoints_need_the_role_and_are_audited(
    client: httpx.AsyncClient,
) -> None:
    _, requester = await user(client, "admin-tests", ["requester"])
    _, admin = await user(client, "admin-tests", ["admin"])
    assert (await client.get("/v1/admin/tools", headers=requester)).status_code == 403
    assert (await client.get("/v1/admin/model-defaults", headers=requester)).status_code == 403

    r = await client.put(
        "/v1/admin/model-defaults/reviewer", json={"model": "any-catalogue-model"}, headers=admin
    )
    assert r.status_code == 200
    roles = {
        m["role"]: m for m in (await client.get("/v1/admin/model-defaults", headers=admin)).json()
    }
    assert (roles["reviewer"]["model"], roles["reviewer"]["source"]) == (
        "any-catalogue-model",
        "admin",
    )

    async with resources(client).sessions.begin() as s:
        s.add(
            ToolRow(
                name="web.search",
                server="web",
                allowed_specialists=["research"],
                input_schema={"type": "object"},
            )
        )
    patched = await client.patch(
        "/v1/admin/tools/web.search", json={"enabled": False}, headers=admin
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False

    csv = await client.get("/v1/admin/audit?format=csv", headers=admin)
    assert csv.headers["content-type"].startswith("text/csv")
    assert "model_default.update" in csv.text
    assert "tool.update" in csv.text


# --- costs and run views (FR-62, A6, SPEC 19.3) ------------------------------------------------


async def record_call(client: httpx.AsyncClient, run_id: str, **extra: Any) -> None:
    async with resources(client).sessions.begin() as s:
        s.add(
            LlmCall(
                run_id=UUID(run_id),
                agent="research",
                model_alias="specialist",
                model_id=extra.get("model_id", "served-model"),
                requested_model="requested-model",
                purpose=extra.get("purpose", "execute"),
                subtask_key="s1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.002"),
                latency_ms=900,
            )
        )


async def test_fr62_costs_and_efficiency(client: httpx.AsyncClient) -> None:
    _, requester = await user(client, "cost-tests", ["requester"])
    created = (await client.post("/v1/tasks", json=TASK, headers=requester)).json()
    await record_call(client, created["run_id"])
    await record_call(client, created["run_id"], purpose="revision")

    assert (await client.get("/v1/costs", headers=requester)).status_code == 403
    _, operator = await user(client, "cost-tests", ["operator"])
    report = (await client.get("/v1/costs?group_by=agent", headers=operator)).json()
    research = next(r for r in report["rows"] if r["key"] == "research")
    assert research["llm_calls"] >= 2

    eff = (await client.get(f"/v1/runs/{created['run_id']}/efficiency", headers=requester)).json()
    assert eff["total"]["calls"] == 2
    assert eff["revisions"]["calls"] == 1
    assert eff["fallbacks"]["calls"] == 2  # served-model differs from requested-model

    graph = (await client.get(f"/v1/runs/{created['run_id']}/graph", headers=requester)).json()
    assert graph["nodes"] == []  # no plan yet
    trace = await client.get(f"/v1/runs/{created['run_id']}/trace", headers=requester)
    assert trace.status_code == 200
    assert trace.json()["root"]["input_tokens"] == 200  # rolled up from both calls


# --- memories (FR-55) ---------------------------------------------------------------------------


async def test_fr55_memory_delete_is_hard_cascades_and_is_audited(
    client: httpx.AsyncClient,
) -> None:
    owner_id, owner = await user(client, "memory-tests", ["requester"])
    _, stranger = await user(client, "memory-tests-2", ["requester"])
    original, summary = uuid.uuid4(), uuid.uuid4()
    async with resources(client).sessions.begin() as s:
        s.add(
            Memory(
                id=original,
                namespace=f"user:{owner_id}",
                kind="preference",
                text="Short reports",
                importance=0.5,
            )
        )
        s.add(
            Memory(
                id=summary,
                namespace=f"user:{owner_id}",
                kind="preference",
                text="Prefers short reports",
                importance=0.6,
                derived_from=[original],
            )
        )
    listed = (await client.get("/v1/memories", headers=owner)).json()
    assert str(original) in [m["id"] for m in listed]
    assert (await client.delete(f"/v1/memories/{original}", headers=stranger)).status_code == 404

    patched = await client.patch(f"/v1/memories/{summary}", json={"importance": 0.9}, headers=owner)
    assert patched.json()["importance"] == pytest.approx(0.9)

    assert (await client.delete(f"/v1/memories/{original}", headers=owner)).status_code == 204
    remaining = [m["id"] for m in (await client.get("/v1/memories", headers=owner)).json()]
    assert str(original) not in remaining
    assert str(summary) not in remaining  # derived summary deleted too (SPEC 13.4)
    async with resources(client).sessions() as s:
        from sqlalchemy import select

        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.target_id == str(original))))
            .scalars()
            .all()
        )
    assert rows
    assert "Short reports" not in str(rows[0].before)  # no memory text in the log


# --- replay (FR-70) -------------------------------------------------------------------------------


async def test_fr70_replay_is_operator_only_and_checks_the_checkpoint(
    client: httpx.AsyncClient,
) -> None:
    _, requester = await user(client, "replay-tests", ["requester"])
    created = (await client.post("/v1/tasks", json=TASK, headers=requester)).json()
    run = created["run_id"]
    body = {"checkpoint_id": "1ef-not-a-checkpoint"}
    assert (
        await client.post(f"/v1/runs/{run}/replay", json=body, headers=requester)
    ).status_code == 403

    _, operator = await user(client, "replay-tests", ["operator"])
    assert (await client.get(f"/v1/runs/{run}/checkpoints", headers=operator)).json() == []
    missing = await client.post(f"/v1/runs/{run}/replay", json=body, headers=operator)
    assert missing.status_code == 404


# --- dev password login ----------------------------------------------------------------------


async def test_password_login_issues_a_working_token(client: httpx.AsyncClient) -> None:
    from orchestrix_core.db.repos.dev_logins import DevLoginRepo

    name = uuid.uuid4().hex[:8]
    email = f"{name}@test.local"
    users = UserRepo(resources(client).sessions)
    row = await users.upsert(f"dev|{email}", email, name)
    await users.ensure_team_member(row.id, "api-tests")
    await DevLoginRepo(resources(client).sessions).set_password(
        name, row.id, "pw-123", ["requester"]
    )
    bad = await client.post("/v1/auth/login", json={"username": name, "password": "nope"})
    assert bad.status_code == 401
    ok = await client.post("/v1/auth/login", json={"username": name, "password": "pw-123"})
    assert ok.status_code == 200
    headers = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert (await client.post("/v1/tasks", json=TASK, headers=headers)).status_code in (
        200,
        201,
        202,
    )
