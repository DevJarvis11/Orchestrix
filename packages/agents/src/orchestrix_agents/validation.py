"""Deterministic plan validation (FR-11): no model involved, errors are fed back to the planner."""

import re
from collections import deque
from typing import Final

from orchestrix_agents.specialists import SPECIALIST_TOOLS
from orchestrix_core.schemas import Plan

_OUTPUT_REF: Final = re.compile(r"^(s\d{1,2})\.output$")
_ATTACHMENT_REF: Final = re.compile(r"^attachment:(.+)$")
ATTACHMENT_TOOL: Final = "files.read_artifact"


def find_cycle(plan: Plan) -> list[str]:
    """Kahn's algorithm. Returns the keys left in a cycle (empty when the DAG is acyclic)."""
    keys = {s.key for s in plan.subtasks}
    indegree = {s.key: 0 for s in plan.subtasks}
    children: dict[str, list[str]] = {k: [] for k in keys}
    for s in plan.subtasks:
        for dep in s.depends_on:
            if dep in keys:
                indegree[s.key] += 1
                children[dep].append(s.key)
    queue = deque(k for k, d in indegree.items() if d == 0)
    while queue:
        key = queue.popleft()
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return sorted(k for k, d in indegree.items() if d > 0)


def validate_plan(plan: Plan | None, attachments: list[str]) -> list[str]:
    """Return every validation error; an empty list means the plan may run."""
    if plan is None:
        return ["no valid plan was produced"]
    errors: list[str] = []
    seen: set[str] = set()
    for s in plan.subtasks:
        if s.key in seen:
            errors.append(f"duplicate subtask key {s.key}")
        seen.add(s.key)

    for s in plan.subtasks:
        if s.specialist not in SPECIALIST_TOOLS:
            errors.append(f"{s.key}: unknown specialist {s.specialist!r}")
            continue
        for dep in s.depends_on:
            if dep == s.key:
                errors.append(f"{s.key}: depends on itself")
            elif dep not in seen:
                errors.append(f"{s.key}: depends on unknown subtask {dep}")
        for name, ref in s.inputs.items():
            if m := _OUTPUT_REF.match(ref):
                if m.group(1) not in s.depends_on:
                    errors.append(
                        f"{s.key}: input {name!r} references {ref} but {m.group(1)} is not in depends_on"
                    )
            elif m := _ATTACHMENT_REF.match(ref):
                if m.group(1) not in attachments:
                    errors.append(
                        f"{s.key}: input {name!r} references unknown attachment {m.group(1)!r}"
                    )
                if ATTACHMENT_TOOL not in SPECIALIST_TOOLS[s.specialist]:
                    errors.append(
                        f"{s.key}: specialist {s.specialist} may not use {ATTACHMENT_TOOL} for {ref}"
                    )
            else:
                errors.append(
                    f"{s.key}: input {name!r} has unresolvable reference {ref!r} "
                    "(use '<key>.output' or 'attachment:<name>')"
                )

    if cycle := find_cycle(plan):
        errors.append(f"dependency cycle among {', '.join(cycle)}")
    return errors
