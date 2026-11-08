"""Graph nodes, one module each (SPEC 11.3)."""

from orchestrix_agents.nodes.clarify import clarify
from orchestrix_agents.nodes.dispatch import dispatch
from orchestrix_agents.nodes.final_gate import final_gate
from orchestrix_agents.nodes.final_review import final_review
from orchestrix_agents.nodes.finalize import finalize
from orchestrix_agents.nodes.intake import intake
from orchestrix_agents.nodes.plan import plan
from orchestrix_agents.nodes.plan_gate import plan_gate
from orchestrix_agents.nodes.retrieve_memory import retrieve_memory
from orchestrix_agents.nodes.synthesize import synthesize
from orchestrix_agents.nodes.validate_plan import validate_plan
from orchestrix_agents.nodes.write_memory import write_memory

NODES = {
    "intake": intake,
    "retrieve_memory": retrieve_memory,
    "plan": plan,
    "clarify": clarify,
    "validate_plan": validate_plan,
    "plan_gate": plan_gate,
    "dispatch": dispatch,
    "synthesize": synthesize,
    "final_review": final_review,
    "final_gate": final_gate,
    "write_memory": write_memory,
    "finalize": finalize,
}

__all__ = ["NODES"]
