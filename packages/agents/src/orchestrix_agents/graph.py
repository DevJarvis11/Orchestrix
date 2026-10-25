"""build_graph(): parent StateGraph wiring (SPEC 11.4)."""

# langgraph is a namespace package, so pyright ignores its py.typed for langgraph.graph.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes import NODES
from orchestrix_agents.nodes.dispatch import route_dispatch
from orchestrix_agents.nodes.final_review import route_final
from orchestrix_agents.nodes.plan_gate import route_after_gate
from orchestrix_agents.nodes.validate_plan import route_after_validate
from orchestrix_agents.state import RunState
from orchestrix_agents.subgraph import build_subtask_graph

type RunGraph = CompiledStateGraph[RunState, RunContext, RunState, RunState]


def build_graph(checkpointer: BaseCheckpointSaver[str] | None = None) -> RunGraph:
    g = StateGraph(RunState, context_schema=RunContext)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_node("subtask_worker", build_subtask_graph())
    g.add_edge(START, "intake")
    g.add_edge("intake", "retrieve_memory")
    g.add_edge("retrieve_memory", "plan")
    g.add_edge("plan", "validate_plan")
    g.add_conditional_edges("validate_plan", route_after_validate, ["plan", "plan_gate"])
    # Extends SPEC 11.4: an edited plan that is still invalid returns to plan_gate, and a
    # rejected plan stops the run.
    g.add_conditional_edges("plan_gate", route_after_gate, ["plan_gate", "dispatch", "finalize"])
    g.add_conditional_edges(
        "dispatch", route_dispatch, ["subtask_worker", "synthesize", "finalize"]
    )
    g.add_edge("subtask_worker", "dispatch")
    g.add_edge("synthesize", "final_review")
    g.add_conditional_edges("final_review", route_final, ["synthesize", "write_memory", "finalize"])
    g.add_edge("write_memory", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
