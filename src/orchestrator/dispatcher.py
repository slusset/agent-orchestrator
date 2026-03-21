"""
Agent Dispatcher: Launches agents as separate async tasks.

The dispatcher is the bridge between the graph's dispatch nodes
and the actual agent processes. It:
1. Deserializes the TaskBundle from the graph state
2. Creates the appropriate agent instance
3. Injects a StatusReporter
4. Launches the agent in a background task

The dispatcher runs alongside the graph (not inside it).
The graph dispatches → interrupts, and the dispatcher picks up
the bundle and launches the agent independently.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.coding_agent import CodingAgent
from src.contracts.coding_bundle import CodingBundle
from src.contracts.status_reporter import HttpStatusReporter, LogStatusReporter, StatusReporter
from src.contracts.task_bundle import TaskBundle
from src.orchestrator.state import TaskRecord

logger = logging.getLogger(__name__)


# Registry of agent types → (bundle_class, agent_class)
AGENT_REGISTRY: dict[str, tuple[type[TaskBundle], type[BaseAgent]]] = {
    "coding": (CodingBundle, CodingAgent),
    # "uat": (UATBundle, UATAgent),       # TODO
    # "devops": (DevOpsBundle, DevOpsAgent), # TODO
    # "pr": (PRBundle, PRAgent),           # TODO
}


class AgentDispatcher:
    """
    Launches agents from TaskRecords.

    The dispatcher is configured with a transport mode (http or log)
    and maintains a registry of running agents for monitoring.
    """

    def __init__(self, transport: str = "log") -> None:
        """
        Args:
            transport: "http" for real callbacks, "log" for local dev
        """
        self.transport = transport
        self._running: dict[str, asyncio.Task] = {}  # task_id → asyncio.Task

    def _make_reporter(self, bundle: TaskBundle) -> StatusReporter:
        """Create the appropriate StatusReporter based on transport config."""
        if self.transport == "http":
            return HttpStatusReporter(bundle)
        return LogStatusReporter(bundle)

    async def dispatch(self, task_record: TaskRecord) -> None:
        """
        Launch an agent from a TaskRecord.

        Deserializes the bundle, creates the agent, and runs it
        in a background asyncio task.
        """
        agent_type = task_record["agent_type"]
        task_id = task_record["task_id"]

        if agent_type not in AGENT_REGISTRY:
            logger.error("Unknown agent type: %s", agent_type)
            return

        bundle_class, agent_class = AGENT_REGISTRY[agent_type]

        # Deserialize the bundle from the task record
        bundle = bundle_class.model_validate(task_record["bundle"])

        # Create reporter and agent
        reporter = self._make_reporter(bundle)
        agent = agent_class(bundle=bundle, reporter=reporter)

        # Launch in background
        task = asyncio.create_task(
            self._run_agent(agent),
            name=f"agent-{agent_type}-{task_id}",
        )
        self._running[task_id] = task

        logger.info("Launched %s agent for task %s", agent_type, task_id)

    async def _run_agent(self, agent: BaseAgent) -> None:
        """Wrapper that cleans up tracking after agent completes."""
        try:
            await agent.run()
        finally:
            self._running.pop(agent.bundle.task_id, None)

    def is_running(self, task_id: str) -> bool:
        """Check if an agent is still running."""
        task = self._running.get(task_id)
        return task is not None and not task.done()

    def get_running_tasks(self) -> list[str]:
        """Get list of currently running task IDs."""
        return [tid for tid, task in self._running.items() if not task.done()]

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running agent. Returns True if cancelled."""
        task = self._running.get(task_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("Cancelled agent for task %s", task_id)
            return True
        return False

    async def cancel_all(self) -> None:
        """Cancel all running agents. Call on shutdown."""
        for task_id in list(self._running.keys()):
            await self.cancel(task_id)
