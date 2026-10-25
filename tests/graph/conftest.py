"""Graph test harness: run the real graph on fakes with an in-memory checkpointer."""

from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from orchestrix_agents.context import RunContext
from orchestrix_agents.graph import RunGraph, build_graph
from orchestrix_agents.serde import make_serializer
from orchestrix_core.schemas import TaskInput


@dataclass
class Run:
    graph: RunGraph
    ctx: RunContext
    thread: str = "thread-1"
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def config(self) -> RunnableConfig:
        return {"configurable": {"thread_id": self.thread}, "recursion_limit": 200}

    async def _stream(self, value: Any) -> None:
        async for mode, chunk in self.graph.astream(
            value,
            self.config,
            context=self.ctx,
            stream_mode=["custom", "updates"],
            durability="sync",
        ):
            if mode == "custom":
                self.events.append(cast(dict[str, Any], chunk))

    async def start(self, task: TaskInput) -> None:
        await self._stream({"task": task})

    async def resume(self, decision: dict[str, Any], interrupt_id: str | None = None) -> None:
        if interrupt_id is None:
            await self._stream(Command(resume=decision))
        else:
            await self._stream(Command(resume={interrupt_id: decision}))

    async def state(self) -> dict[str, Any]:
        snapshot = await self.graph.aget_state(self.config)
        return dict(snapshot.values)

    async def interrupts(self) -> list[Any]:
        snapshot = await self.graph.aget_state(self.config)
        return list(snapshot.interrupts)

    def event_types(self) -> list[str]:
        return [e["type"] for e in self.events]


@pytest.fixture
def task() -> TaskInput:
    return TaskInput(
        id="task-1", title="Latency report", description="Summarise p95 latency trends."
    )


@pytest.fixture
def make_run():
    def factory(ctx: RunContext) -> Run:
        return Run(graph=build_graph(InMemorySaver(serde=make_serializer())), ctx=ctx)

    return factory
