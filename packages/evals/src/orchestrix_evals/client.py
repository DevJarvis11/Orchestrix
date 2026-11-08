"""Drives the real stack through the public API (SPEC 20.2): submit, poll, decide escalations,
then read results back (events, trace, efficiency, deliverable). No database access needed."""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from orchestrix_evals.jsonutil import obj, seq

DEFAULT_API = "http://127.0.0.1:58100"
TERMINAL = frozenset({"completed", "failed", "cancelled", "budget_exceeded"})
WAITING = frozenset({"awaiting_human", "awaiting_plan_approval"})
FINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})
MIME = {".csv": "text/csv", ".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf"}


def dev_token(email: str, team: str, roles: str) -> str:
    """A dev-mode JWT for a provisioned user (`make dev-token` with explicit roles)."""
    out = subprocess.run(  # noqa: S603 - fixed module, arguments built from our own values
        [
            sys.executable,
            "-m",
            "orchestrix_api.security.dev_jwt",
            "--email",
            email,
            "--team",
            team,
            "--roles",
            roles,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip().splitlines()[-1]


@dataclass(frozen=True)
class Actors:
    requester: str  # bearer token: requester role
    reviewer: str  # bearer token: reviewer of the same team, a different user (four-eyes)

    @classmethod
    def provision(cls, team: str, tag: str) -> "Actors":
        """Static tokens from the environment win (OIDC stacks); otherwise dev tokens."""
        req, rev = (
            os.environ.get("ORCHESTRIX_EVAL_TOKEN"),
            os.environ.get("ORCHESTRIX_EVAL_REVIEWER_TOKEN"),
        )
        if req and rev:
            return cls(req, rev)
        return cls(
            dev_token(f"eval-{tag}@orchestrix.local", team, "requester"),
            dev_token(f"eval-reviewer-{tag}@orchestrix.local", team, "requester,reviewer"),
        )


@dataclass
class Handled:
    trigger: str
    level: str
    action: str
    escalation_id: str


@dataclass
class RunOutcome:
    task_id: str
    run_id: str
    status: str
    failure_reason: str | None
    seconds: float
    escalations: list[Handled] = field(default_factory=list[Handled])
    events: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    trace: dict[str, Any] = field(default_factory=dict[str, Any])
    efficiency: dict[str, Any] = field(default_factory=dict[str, Any])
    deliverable: str = ""
    sources: set[str] = field(default_factory=set[str])
    side_effects: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    langfuse_url: str | None = None
    plan: dict[str, Any] | None = None

    @property
    def cost_usd(self) -> float:
        return float(obj(self.efficiency.get("total")).get("cost_usd", 0.0))

    @property
    def tokens(self) -> int:
        total = obj(self.efficiency.get("total"))
        return int(total.get("input_tokens", 0)) + int(total.get("output_tokens", 0))

    def event_types(self) -> list[str]:
        return [str(e.get("type")) for e in self.events]


# The decision for one open escalation (its GET /v1/escalations/{id} body) -> a decision body.
DecisionPolicy = Callable[[dict[str, Any]], dict[str, Any]]


def approving_policy(clarify_answers: list[str] | None = None) -> DecisionPolicy:
    """Golden tasks: let the run proceed the way a cooperative reviewer would."""

    def decide(esc: dict[str, Any]) -> dict[str, Any]:
        allowed: list[str] = list(esc["payload"].get("allowed_actions", []))
        trigger = str(esc.get("trigger"))
        if "answer" in allowed:
            questions = list(esc["payload"].get("questions", []))
            answers = list(clarify_answers or [])
            answers += ["No preference: use your best judgement."] * (len(questions) - len(answers))
            return {"action": "answer", "answers": answers[: max(1, len(questions))]}
        if trigger == "budget_100":
            return {"action": "reject", "reason": "eval: budget cap is part of the test"}
        if "approve" in allowed:
            return {"action": "approve"}
        if "approve_with_edits" in allowed and esc["payload"].get("proposed", {}).get("plan"):
            return {"action": "approve_with_edits", "edits": esc["payload"]["proposed"]["plan"]}
        return {"action": "reject", "reason": "eval: no approvable action", "on_reject": "skip"}

    return decide


def rejecting_policy() -> DecisionPolicy:
    """Injection cases: a human rejects any action the request never asked for."""

    def decide(esc: dict[str, Any]) -> dict[str, Any]:
        allowed: list[str] = list(esc["payload"].get("allowed_actions", []))
        if "answer" in allowed:
            return {"action": "answer", "answers": ["Just summarise the document."]}
        if esc.get("level") == "approve_plan" and "approve" in allowed:
            return {"action": "approve"}  # plans are not side effects
        if (
            str(esc.get("trigger")) in ("subtask_failed_twice", "low_review_score")
            and "approve" in allowed
        ):
            return {"action": "approve"}
        return {
            "action": "reject",
            "reason": "eval: action not requested by the user",
            "on_reject": "skip",
        }

    return decide


class EvalClient:
    def __init__(
        self, api_url: str, actors: Actors, *, timeout_s: float = 1800, poll_s: float = 3
    ) -> None:
        self._api = api_url.rstrip("/")
        self._actors = actors
        self._timeout = timeout_s
        self._poll = poll_s
        self._http = httpx.AsyncClient(timeout=60)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _h(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _get(self, path: str, token: str | None = None, **params: Any) -> Any:
        r = await self._http.get(
            f"{self._api}{path}", headers=self._h(token or self._actors.requester), params=params
        )
        r.raise_for_status()
        return r.json()

    async def upload(self, name: str, body: bytes) -> str:
        mime = MIME.get(Path(name).suffix.lower(), "application/octet-stream")
        r = await self._http.post(
            f"{self._api}/v1/uploads",
            headers=self._h(self._actors.requester),
            files={"file": (name, body, mime)},
        )
        r.raise_for_status()
        return str(r.json()["artifact_id"])

    async def submit(self, body: dict[str, Any]) -> tuple[str, str]:
        r = await self._http.post(
            f"{self._api}/v1/tasks", headers=self._h(self._actors.requester), json=body
        )
        r.raise_for_status()
        data = r.json()
        return str(data["task_id"]), str(data["run_id"])

    async def _decide_open(
        self, run_id: str, policy: DecisionPolicy, handled: list[Handled]
    ) -> None:
        seen = {h.escalation_id for h in handled}
        for token in (self._actors.requester, self._actors.reviewer):  # clarify -> requester
            inbox: list[dict[str, Any]] = await self._get("/v1/escalations", token, status="open")
            for item in inbox:
                if item["run_id"] != run_id or item["id"] in seen:
                    continue
                detail: dict[str, Any] = await self._get(f"/v1/escalations/{item['id']}", token)
                if not detail.get("can_decide"):
                    continue
                body = policy(detail)
                r = await self._http.post(
                    f"{self._api}/v1/escalations/{item['id']}/decision",
                    headers=self._h(token),
                    json=body,
                )
                if r.status_code in (202, 409):
                    handled.append(
                        Handled(
                            str(item["trigger"]),
                            str(item["level"]),
                            str(body["action"]),
                            str(item["id"]),
                        )
                    )
                    seen.add(item["id"])

    async def wait(self, task_id: str, run_id: str, policy: DecisionPolicy) -> RunOutcome:
        started = time.monotonic()
        handled: list[Handled] = []
        detail: dict[str, Any] = {}
        while time.monotonic() - started < self._timeout:
            detail = await self._get(f"/v1/tasks/{task_id}")
            if detail["status"] in TERMINAL:
                break
            if detail["status"] in WAITING:
                await self._decide_open(run_id, policy, handled)
            await asyncio.sleep(self._poll)
        else:
            await self._http.post(
                f"{self._api}/v1/tasks/{task_id}/cancel", headers=self._h(self._actors.requester)
            )
            detail = await self._get(f"/v1/tasks/{task_id}")
        outcome = RunOutcome(
            task_id=task_id,
            run_id=run_id,
            status=str(detail.get("status")),
            failure_reason=detail.get("failure_reason"),
            seconds=time.monotonic() - started,
            escalations=handled,
            plan=detail.get("plan"),
        )
        await self._collect(outcome)
        return outcome

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        """Replay the durable event log (SSE). The server closes the stream after a final event."""
        out: list[dict[str, Any]] = []
        block: dict[str, str] = {}
        async with self._http.stream(
            "GET",
            f"{self._api}/v1/runs/{run_id}/events",
            headers=self._h(self._actors.requester),
            timeout=30,
        ) as stream:
            async for line in stream.aiter_lines():
                if line == "":
                    if "data" in block:
                        data: dict[str, Any] = json.loads(block["data"])
                        out.append(data)
                        if data.get("type") in FINAL_EVENTS:
                            break
                    block = {}
                elif not line.startswith(":") and ": " in line:
                    key, value = line.split(": ", 1)
                    block[key] = value
        return out

    async def _collect(self, o: RunOutcome) -> None:
        try:
            o.events = await asyncio.wait_for(self.events(o.run_id), 60)
        except (TimeoutError, httpx.HTTPError):
            o.events = []
        o.trace = await self._get(f"/v1/runs/{o.run_id}/trace")
        o.langfuse_url = o.trace.get("langfuse_url")
        o.efficiency = await self._get(f"/v1/runs/{o.run_id}/efficiency")
        if o.status == "completed":
            r = await self._http.get(
                f"{self._api}/v1/tasks/{o.task_id}/deliverable",
                headers=self._h(self._actors.requester),
                follow_redirects=True,  # httpx drops Authorization on the cross-origin hop
            )
            if r.status_code == 200:
                o.deliverable = r.text
        for node in o.trace.get("root", {}).get("children", []):
            if node.get("kind") != "subtask":
                continue
            sub_id = str(node["id"]).split(":", 1)[1]
            row = await self._get(f"/v1/runs/{o.run_id}/trace/subtask/{sub_id}")
            result = obj(obj(row.get("data")).get("result"))
            o.sources |= {str(s) for s in seq(result.get("sources"))}
            for child in node.get("children", []):
                if child.get("kind") == "tool_call" and child.get("label") in SIDE_EFFECT_TOOLS:
                    tc_id = str(child["id"]).split(":", 1)[1]
                    full = await self._get(f"/v1/runs/{o.run_id}/trace/tool_call/{tc_id}")
                    o.side_effects.append(dict(full.get("data") or {}))

    async def memories(self) -> list[dict[str, Any]]:
        """The requester's view: their own (user-namespace) memories."""
        return list(await self._get("/v1/memories"))

    async def memories_as_reviewer(self) -> list[dict[str, Any]]:
        """Team memories are visible to the team's reviewers (SPEC 2)."""
        return list(await self._get("/v1/memories", self._actors.reviewer))


SIDE_EFFECT_TOOLS = frozenset({"comms.send_email", "files.write_artifact"})


async def run_task(
    client: EvalClient,
    *,
    title: str,
    description: str,
    attachments: list[tuple[str, bytes]],
    models: dict[str, str],
    policy: DecisionPolicy,
    extra: dict[str, Any] | None = None,
) -> RunOutcome:
    ids = [await client.upload(name, body) for name, body in attachments]
    task_id, run_id = await client.submit(
        {
            "title": title[:200],
            "description": description,
            "attachments": ids,
            "model_overrides": models,
            **(extra or {}),
        }
    )
    return await client.wait(task_id, run_id, policy)


Runner = Callable[[], Awaitable[RunOutcome]]
