"""PolicyClient (SPEC 15.1): OPA over HTTP with a 200 ms timeout. Every failure means deny."""

from typing import Any

import httpx
from pydantic import BaseModel

from orchestrix_core.logging import get_logger
from orchestrix_core.schemas import PolicyDecision

log = get_logger(__name__)

TOOL_DECISION = "orchestrix/tool/decision"
ESCALATION_ALLOW = "orchestrix/escalation/allow"
DEFAULT_TIMEOUT = 0.2


class ToolPolicyInput(BaseModel):
    tool: str
    specialist: str
    args: dict[str, Any]
    user: str
    team: str
    calls_last_minute: int
    context_tainted: bool


class Actor(BaseModel):
    id: str
    roles: list[str]
    team_ids: list[str]


class EscalationPolicyInput(BaseModel):
    actor: Actor
    escalation: dict[str, Any]  # {level, assignee_team_id}
    task: dict[str, Any]  # {requester_id}


class PolicyClient:
    def __init__(
        self, opa_url: str, http: httpx.AsyncClient, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        self._base = opa_url.rstrip("/")
        self._http = http
        self._timeout = timeout

    async def _query(self, path: str, data: BaseModel) -> Any:
        response = await self._http.post(
            f"{self._base}/v1/data/{path}",
            json={"input": data.model_dump(mode="json")},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json().get("result")

    async def tool_decision(self, data: ToolPolicyInput) -> PolicyDecision:
        """FR-90. OPA unreachable, slow or undefined -> deny (fail closed)."""
        try:
            result = await self._query(TOOL_DECISION, data)
            if not isinstance(result, dict):
                return PolicyDecision.deny("policy returned no decision")
            return PolicyDecision.model_validate(result)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("policy engine unavailable; denying", tool=data.tool, error=str(exc))
            return PolicyDecision.deny("policy engine unavailable")

    async def may_decide(self, data: EscalationPolicyInput) -> bool:
        """Who may decide an escalation (four-eyes rule). Fails closed."""
        try:
            return (await self._query(ESCALATION_ALLOW, data)) is True
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("policy engine unavailable; denying decision", error=str(exc))
            return False

    async def push(self, path: str, value: Any) -> None:
        """PUT /v1/data/<path>: the tool registry and org data live in OPA as data."""
        response = await self._http.put(f"{self._base}/v1/data/{path}", json=value, timeout=5)
        response.raise_for_status()


def tools_document(rows: list[Any]) -> dict[str, Any]:
    """The `tools` table as OPA data (rows are orchestrix_core.db.models.ToolRow)."""
    return {
        r.name: {
            "server": r.server,
            "enabled": r.enabled,
            "sensitive": r.sensitive,
            "allowed_specialists": list(r.allowed_specialists),
            "rate_limit_per_min": r.rate_limit_per_min,
        }
        for r in rows
    }
