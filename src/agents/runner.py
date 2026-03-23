"""
Agent Runner: Wraps a BaseAgent as an HTTP service.

This is the JSON-RPC-style invocation layer. The PA dispatches
TaskBundles via HTTP POST to the agent's /execute endpoint.
The agent runs, sends heartbeats via callback, and the result
is delivered asynchronously — not as the HTTP response.

The HTTP response is just an acknowledgment: "task accepted."
The real result comes via the callback channel (AsyncAPI pattern).

Invocation styles:
    LOCAL:  dispatcher.dispatch(task_record)  →  asyncio.create_task(agent.run())
    HTTP:   POST /execute {bundle}            →  202 Accepted + background work
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.agents.base import BaseAgent
from src.agents.coding_agent import CodingAgent
from src.agents.stub_agent import StubAgent
from src.contracts.coding_bundle import CodingBundle
from src.contracts.status_reporter import HttpStatusReporter, LogStatusReporter
from src.contracts.task_bundle import TaskBundle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON-RPC-style request/response models
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    """JSON-RPC-style request to execute a task."""

    agent_type: str
    bundle: dict[str, Any]


class ExecuteResponse(BaseModel):
    """Acknowledgment that the task was accepted."""

    task_id: str
    status: str = "accepted"
    message: str = "Task dispatched to agent"


class AgentStatus(BaseModel):
    """Current status of the agent runner."""

    agent_type: str
    running_tasks: list[str]
    max_concurrent: int
    available: bool


class CancelResponse(BaseModel):
    """Response to a cancel request."""

    task_id: str
    cancelled: bool


# ---------------------------------------------------------------------------
# Agent Runner registry
# ---------------------------------------------------------------------------

# Map agent_type → (bundle_class, agent_class)
RUNNER_REGISTRY: dict[str, tuple[type[TaskBundle], type[BaseAgent]]] = {
    "coding": (CodingBundle, CodingAgent),
    "stub": (TaskBundle, StubAgent),
}


# ---------------------------------------------------------------------------
# Agent Runner
# ---------------------------------------------------------------------------


class AgentRunner:
    """
    Manages agent lifecycle for a single agent type.

    Can run standalone (as an HTTP service) or be embedded in
    the orchestrator process for local invocation.
    """

    def __init__(
        self,
        agent_type: str,
        max_concurrent: int = 1,
        transport: str = "http",
    ) -> None:
        self.agent_type = agent_type
        self.max_concurrent = max_concurrent
        self.transport = transport
        self._running: dict[str, asyncio.Task] = {}

        if agent_type not in RUNNER_REGISTRY:
            raise ValueError(f"Unknown agent type: {agent_type}")

        self._bundle_class, self._agent_class = RUNNER_REGISTRY[agent_type]

    @property
    def available(self) -> bool:
        """Can this runner accept a new task?"""
        active = sum(1 for t in self._running.values() if not t.done())
        return active < self.max_concurrent

    @property
    def running_task_ids(self) -> list[str]:
        return [tid for tid, t in self._running.items() if not t.done()]

    async def execute(self, bundle_data: dict[str, Any]) -> str:
        """
        Accept and execute a task. Returns task_id.

        The actual work runs in the background. Results are delivered
        via the callback URL in the bundle, not as a return value.
        """
        if not self.available:
            raise RuntimeError(
                f"Agent {self.agent_type} at capacity "
                f"({self.max_concurrent} concurrent tasks)"
            )

        bundle = self._bundle_class.model_validate(bundle_data)

        # Create reporter based on transport
        if self.transport == "http":
            reporter = HttpStatusReporter(bundle)
        else:
            reporter = LogStatusReporter(bundle)

        agent = self._agent_class(bundle=bundle, reporter=reporter)

        # Launch in background
        task = asyncio.create_task(
            self._run_agent(agent),
            name=f"runner-{self.agent_type}-{bundle.task_id}",
        )
        self._running[bundle.task_id] = task

        logger.info(
            "[runner:%s] Accepted task %s: %s",
            self.agent_type,
            bundle.task_id,
            bundle.objective,
        )

        return bundle.task_id

    async def _run_agent(self, agent: BaseAgent) -> None:
        """Run agent and clean up tracking."""
        try:
            await agent.run()
        finally:
            self._running.pop(agent.bundle.task_id, None)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._running.get(task_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        return False

    async def shutdown(self) -> None:
        """Cancel all running tasks."""
        for task_id in list(self._running.keys()):
            await self.cancel(task_id)


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_agent_app(
    agent_type: str,
    max_concurrent: int = 1,
    transport: str = "http",
) -> FastAPI:
    """
    Create a FastAPI app that serves a single agent type.

    Run with:
        uvicorn src.agents.runner:app --port 8001

    Or programmatically:
        app = create_agent_app("coding")
        uvicorn.run(app, port=8001)
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await runner.shutdown()

    app = FastAPI(
        title=f"Agent Runner: {agent_type}",
        description=f"JSON-RPC-style service for the {agent_type} agent",
        lifespan=lifespan,
    )
    runner = AgentRunner(agent_type, max_concurrent, transport)

    @app.post("/execute", response_model=ExecuteResponse)
    async def execute(request: ExecuteRequest) -> ExecuteResponse:
        """
        Dispatch a task to the agent.

        Returns 202 Accepted immediately. The agent works in the
        background and delivers results via the callback_url in
        the TaskBundle.
        """
        if request.agent_type != agent_type:
            raise HTTPException(
                status_code=400,
                detail=f"This runner serves {agent_type}, not {request.agent_type}",
            )

        if not runner.available:
            raise HTTPException(
                status_code=429,
                detail=f"Agent at capacity ({max_concurrent} concurrent tasks)",
            )

        task_id = await runner.execute(request.bundle)

        return ExecuteResponse(task_id=task_id)

    @app.get("/status", response_model=AgentStatus)
    async def status() -> AgentStatus:
        """Check the agent runner's current status."""
        return AgentStatus(
            agent_type=agent_type,
            running_tasks=runner.running_task_ids,
            max_concurrent=max_concurrent,
            available=runner.available,
        )

    @app.post("/cancel/{task_id}", response_model=CancelResponse)
    async def cancel(task_id: str) -> CancelResponse:
        """Cancel a running task."""
        cancelled = await runner.cancel(task_id)
        return CancelResponse(task_id=task_id, cancelled=cancelled)

    return app


# Default app for `uvicorn src.agents.runner:app`
app = create_agent_app("coding", max_concurrent=1, transport="http")
