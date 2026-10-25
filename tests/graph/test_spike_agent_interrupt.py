"""De-risk spike (plan M2): a create_agent run inside a Send worker interrupts and resumes
without repeating the tool calls it already made (SPEC 12.2, 11.6). Kept as a permanent test."""

import operator
from typing import Annotated, Any, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command, Send, interrupt

from tests.fixtures.scripted_model import ScriptedChatModel

calls: dict[str, int] = {}


@tool
def fetch(source: str) -> str:
    """Fetch a source (counts how often it really runs)."""
    calls[source] = calls.get(source, 0) + 1
    return f"content of {source}"


@tool
def send_email(to: str) -> str:
    """Sensitive: asks a human first (what PolicyMiddleware will do in M4)."""
    decision = interrupt({"tool": "send_email", "to": to})
    calls["send_email"] = calls.get("send_email", 0) + 1
    return f"sent to {to}: {decision}"


def ai_tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class Parent(TypedDict, total=False):
    keys: list[str]
    outputs: Annotated[list[str], operator.add]


class WorkerIn(TypedDict):
    key: str


def make_agent(key: str) -> Any:
    model = ScriptedChatModel(
        script=[
            ai_tool_call("fetch", {"source": f"{key}-a"}, f"{key}-1"),
            ai_tool_call("send_email", {"to": f"{key}@x"}, f"{key}-2"),
            AIMessage(content=f"done {key}"),
        ]
    )
    return create_agent(model=model, tools=[fetch, send_email])


AGENTS: dict[str, Any] = {}


async def worker(state: WorkerIn, config: RunnableConfig) -> dict[str, Any]:
    agent = AGENTS[state["key"]]
    result = await agent.ainvoke({"messages": [HumanMessage(state["key"])]}, config)
    return {"outputs": [str(result["messages"][-1].content)]}


def fan_out(state: Parent) -> list[Send]:
    return [Send("worker", {"key": k}) for k in state.get("keys", [])]


def build() -> Any:
    g = StateGraph(Parent)
    g.add_node("worker", worker)
    g.add_conditional_edges(START, fan_out, ["worker"])
    return g.compile(checkpointer=InMemorySaver())


async def test_agent_in_send_worker_resumes_without_repeating_tool_calls() -> None:
    calls.clear()
    AGENTS.clear()
    AGENTS.update({k: make_agent(k) for k in ("s1", "s2")})
    graph = build()
    config: RunnableConfig = {"configurable": {"thread_id": "spike"}}

    await graph.ainvoke({"keys": ["s1", "s2"]}, config)
    pending = (await graph.aget_state(config)).interrupts
    assert sorted(i.value["to"] for i in pending) == ["s1@x", "s2@x"]
    assert calls == {"s1-a": 1, "s2-a": 1}  # fetched once each before pausing

    by_to = {i.value["to"]: i.id for i in pending}
    await graph.ainvoke(Command(resume={by_to["s2@x"]: "approved"}), config)
    await graph.ainvoke(Command(resume={by_to["s1@x"]: "approved"}), config)

    state = await graph.aget_state(config)
    assert sorted(state.values["outputs"]) == ["done s1", "done s2"]
    assert calls == {"s1-a": 1, "s2-a": 1, "send_email": 2}  # nothing before the interrupt re-ran
