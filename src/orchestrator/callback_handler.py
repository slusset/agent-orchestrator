"""
Callback Handler: Receives status updates and results from agents.

This is the PA's webhook endpoint. Agents POST StatusUpdate and TaskResult
payloads here. The handler updates the orchestrator's checkpoint state
so the graph can resume and make routing decisions.

Runs as a lightweight async server alongside the LangGraph graph.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.contracts.task_bundle import StatusUpdate, TaskResult, TaskStatus
from src.orchestrator.state import TaskRecord

logger = logging.getLogger(__name__)


class CallbackHandler:
    """
    Processes incoming status updates and task results from agents.

    In production, this sits behind an HTTP server (FastAPI, etc.).
    The handler validates the payload and produces state updates
    that the orchestrator graph can consume.

    Decoupled from the HTTP layer so it can be tested independently
    and used with different server frameworks.
    """

    def __init__(self) -> None:
        self._pending_updates: list[TaskRecord] = []
        self._completion_callbacks: dict[str, list[Any]] = {}

    def on_completion(self, task_id: str, callback: Any) -> None:
        """Register a callback to fire when a task completes."""
        self._completion_callbacks.setdefault(task_id, []).append(callback)

    def handle_status_update(self, payload: dict[str, Any]) -> TaskRecord:
        """
        Process a StatusUpdate from an agent.

        Returns a TaskRecord patch that the orchestrator can merge
        into its state.
        """
        update = StatusUpdate.model_validate(payload)
        logger.info("[%s] Status update: %s — %s", update.task_id, update.status.value, update.message)

        record = TaskRecord(
            task_id=update.task_id,
            agent_type="",  # Preserved from existing record by merge_tasks
            bundle={},
            status=update.status.value,
            last_update=update.model_dump(mode="json"),
            result=None,
            pr_url=None,
        )
        self._pending_updates.append(record)
        return record

    def handle_task_result(self, payload: dict[str, Any]) -> TaskRecord:
        """
        Process a TaskResult from an agent.

        Returns a TaskRecord patch with completion info.
        Fires any registered completion callbacks.
        """
        result = TaskResult.model_validate(payload)
        status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        logger.info("[%s] Task result: success=%s — %s", result.task_id, result.success, result.summary)

        # Extract PR URL from artifacts if present
        pr_url = next((a for a in result.artifacts if "pull" in a or "/pr/" in a), None)

        record = TaskRecord(
            task_id=result.task_id,
            agent_type="",
            bundle={},
            status=status.value,
            last_update=None,
            result=result.model_dump(mode="json"),
            pr_url=pr_url,
        )
        self._pending_updates.append(record)

        # Fire completion callbacks
        for cb in self._completion_callbacks.pop(result.task_id, []):
            try:
                cb(result)
            except Exception:
                logger.warning("Completion callback failed for task %s", result.task_id, exc_info=True)

        return record

    def drain_updates(self) -> list[TaskRecord]:
        """Return and clear all pending state updates."""
        updates = self._pending_updates.copy()
        self._pending_updates.clear()
        return updates
