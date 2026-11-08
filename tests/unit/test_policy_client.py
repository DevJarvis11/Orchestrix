"""PolicyClient (SPEC 15.1, FR-90): OPA decides; anything else is a deny (fail closed)."""

import httpx
import pytest
import respx

from orchestrix_policy.client import Actor, EscalationPolicyInput, PolicyClient, ToolPolicyInput
from orchestrix_policy.escalation_policy import load_escalation_policy

OPA = "http://opa.test"
TOOL = ToolPolicyInput(
    tool="web.search",
    specialist="research",
    args={},
    user="u",
    team="t",
    calls_last_minute=0,
    context_tainted=False,
)
ESC = EscalationPolicyInput(
    actor=Actor(id="u", roles=["reviewer"], team_ids=["t"]),
    escalation={"level": "approve_action", "assignee_team_id": "t"},
    task={"requester_id": "x"},
)


@respx.mock
async def test_fr90_decision_comes_from_opa() -> None:
    respx.post(f"{OPA}/v1/data/orchestrix/tool/decision").respond(
        json={"result": {"result": "require_approval", "reasons": ["sensitive tool"]}}
    )
    async with httpx.AsyncClient() as http:
        decision = await PolicyClient(OPA, http).tool_decision(TOOL)
    assert (decision.result, decision.reasons) == ("require_approval", ["sensitive tool"])


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, json={}),  # undefined decision (no policy loaded)
        httpx.Response(200, json={"result": "nonsense"}),
    ],
)
@respx.mock
async def test_fr90_fails_closed_on_bad_answers(response: httpx.Response) -> None:
    respx.post(f"{OPA}/v1/data/orchestrix/tool/decision").mock(return_value=response)
    async with httpx.AsyncClient() as http:
        decision = await PolicyClient(OPA, http).tool_decision(TOOL)
    assert decision.result == "deny"


@respx.mock
async def test_fr90_fails_closed_when_opa_is_down_or_slow() -> None:
    respx.post(f"{OPA}/v1/data/orchestrix/tool/decision").mock(
        side_effect=httpx.ConnectTimeout("slow")
    )
    respx.post(f"{OPA}/v1/data/orchestrix/escalation/allow").mock(
        side_effect=httpx.ConnectError("down")
    )
    async with httpx.AsyncClient() as http:
        client = PolicyClient(OPA, http)
        assert (await client.tool_decision(TOOL)).reasons == ["policy engine unavailable"]
        assert await client.may_decide(ESC) is False


@respx.mock
async def test_escalation_allow_must_be_literally_true() -> None:
    route = respx.post(f"{OPA}/v1/data/orchestrix/escalation/allow")
    async with httpx.AsyncClient() as http:
        client = PolicyClient(OPA, http)
        route.respond(json={"result": True})
        assert await client.may_decide(ESC) is True
        route.respond(json={"result": "yes"})
        assert await client.may_decide(ESC) is False


def test_escalation_policy_maps_every_spec_trigger() -> None:
    policy = load_escalation_policy()
    assert policy.levels["sensitive_tool"] == "approve_action"
    assert policy.levels["budget_80"] == "notify"
    assert policy.levels["ambiguous_request"] == "clarify"
    assert (policy.sla_hours, policy.timeout_hours) == (24, 72)
