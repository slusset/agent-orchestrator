"""
BaseAgent: Abstract base for all specialized agents.

Each agent:
1. Receives a TaskBundle
2. Gets a StatusReporter (injected, transport-agnostic)
3. Does its work with automatic heartbeats
4. Reports completion/failure via the reporter

Agents run as separate processes from the orchestrator graph.
They are completely decoupled — they don't know about LangGraph,
the state machine, or Postgres. They only know their TaskBundle
and how to call reporter.heartbeat() / reporter.complete().
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from src.contracts.status_reporter import HeartbeatRunner, LogStatusReporter, StatusReporter
from src.contracts.task_bundle import TaskBundle

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base for specialized agents.

    Subclasses implement `execute()` with their domain logic.
    The base class handles lifecycle: heartbeats, error handling,
    and result reporting.
    """

    agent_type: str = "base"  # Override in subclasses

    def __init__(self, bundle: TaskBundle, reporter: StatusReporter | None = None) -> None:
        self.bundle = bundle
        self.reporter = reporter or LogStatusReporter(bundle)

    @abstractmethod
    async def execute(self) -> dict[str, Any]:
        """
        Do the actual work. Implemented by each agent type.

        Returns a dict with:
            - summary: str — what was accomplished
            - artifacts: list[str] — PR URLs, file paths, etc.
            - metadata: dict — any additional info

        Raise an exception to signal failure — the base class
        will catch it and report via the reporter.
        """

    async def run(self) -> None:
        """
        Full lifecycle: heartbeat → execute → report result.

        This is the entry point — callers invoke agent.run(),
        never agent.execute() directly.
        """
        logger.info("[%s] Agent starting task %s: %s", self.agent_type, self.bundle.task_id, self.bundle.objective)

        try:
            async with HeartbeatRunner(self.reporter, message=f"{self.agent_type} working"):
                result = await self.execute()

            await self.reporter.complete(
                summary=result.get("summary", "Task completed"),
                artifacts=result.get("artifacts", []),
                **result.get("metadata", {}),
            )
            logger.info("[%s] Task %s completed successfully", self.agent_type, self.bundle.task_id)

        except Exception as e:
            logger.error("[%s] Task %s failed: %s", self.agent_type, self.bundle.task_id, e, exc_info=True)
            await self.reporter.fail(
                summary=f"Agent failed: {e}",
                errors=[str(e)],
            )
