"""Subtask worker subgraph (SPEC 11.5): execute -> review -> revise loop -> escalate."""

# langgraph is a namespace package, so pyright ignores its py.typed for langgraph.graph.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrix_agents.context import RunContext
from orchestrix_agents.nodes.escalate import escalate, route_after_escalate
from orchestrix_agents.nodes.execute_subtask import execute_subtask, route_after_execute
from orchestrix_agents.nodes.review_subtask import review_subtask, route_after_review
from orchestrix_agents.state import WorkerOutput, WorkerState


def build_subtask_graph() -> CompiledStateGraph[WorkerState, RunContext, WorkerState, WorkerOutput]:
    g = StateGraph(WorkerState, context_schema=RunContext, output_schema=WorkerOutput)
    g.add_node("execute", execute_subtask)
    g.add_node("review", review_subtask)
    g.add_node("escalate", escalate)
    g.add_edge(START, "execute")
    g.add_conditional_edges("execute", route_after_execute, ["execute", "review", "escalate"])
    g.add_conditional_edges("review", route_after_review, ["execute", "escalate", END])
    g.add_conditional_edges("escalate", route_after_escalate, ["execute", END])
    return g.compile()  # inherits the parent's checkpointer
