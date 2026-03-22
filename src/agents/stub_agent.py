"""
Stub Agent: Minimal hello-world agent for smoke testing.

Does no real work — accepts a TaskBundle, sends a couple heartbeats,
sleeps briefly, and returns success. Proves the full plumbing works:
  PA dispatch → agent receives bundle → heartbeats → result → PA resumes

No LLM, no git, no external dependencies. Just the communication loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.contracts.status_reporter import StatusReporter
from src.contracts.task_bundle import TaskBundle

logger = logging.getLogger(__name__)


class StubAgent(BaseAgent):
    """
    Hello-world agent that proves the dispatch↔callback loop works.

    Configurable via bundle.metadata:
        work_seconds: float — how long to simulate work (default: 1.0)
        fail: bool — if True, raise an error instead of succeeding (default: False)
        fail_message: str — error message when fail=True
    """

    agent_type = "stub"

    def __init__(
        self, bundle: TaskBundle, reporter: StatusReporter | None = None
    ) -> None:
        super().__init__(bundle, reporter)

    async def execute(self) -> dict[str, Any]:
        """
        Simulate a minimal work cycle.

        1. Report "starting" heartbeat
        2. Sleep for work_seconds
        3. Report "finishing" heartbeat
        4. Return success (or raise if fail=True)
        """
        meta = self.bundle.metadata
        work_seconds = meta.get("work_seconds", 1.0)
        should_fail = meta.get("fail", False)
        fail_message = meta.get("fail_message", "Stub agent intentional failure")

        logger.info(
            "[stub] Starting task %s: %s (work_seconds=%.1f, fail=%s)",
            self.bundle.task_id,
            self.bundle.objective,
            work_seconds,
            should_fail,
        )

        # Phase 1: Starting
        await self.reporter.heartbeat(
            "Starting work on objective", progress_pct=10
        )

        # Phase 2: Working (simulate with sleep)
        await asyncio.sleep(work_seconds / 2)
        await self.reporter.heartbeat(
            "Halfway through work", progress_pct=50
        )

        await asyncio.sleep(work_seconds / 2)

        # Phase 3: Done or fail
        if should_fail:
            raise RuntimeError(fail_message)

        await self.reporter.heartbeat(
            "Wrapping up", progress_pct=90
        )

        return {
            "summary": f"Stub agent completed: {self.bundle.objective}",
            "artifacts": [f"stub://task/{self.bundle.task_id}/done"],
            "metadata": {
                "work_seconds": work_seconds,
                "agent_type": "stub",
            },
        }
