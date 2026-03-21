"""
StatusReporter: Transport-agnostic abstraction for agents to report back to the PA.

Agents call `reporter.heartbeat()` and `reporter.complete()` — they don't
know or care whether the transport is HTTP, websocket, or a message queue.

The reporter is initialized from a TaskBundle (which carries the callback_url
and status_interval), keeping the agent decoupled from transport details.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from .task_bundle import StatusUpdate, TaskBundle, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class StatusReporter(ABC):
    """
    Abstract base for reporting task status back to the PA.

    Agents receive a StatusReporter instance — they never construct one
    directly. The orchestrator picks the concrete transport implementation.
    """

    def __init__(self, bundle: TaskBundle) -> None:
        self.task_id = bundle.task_id
        self.callback_url = bundle.callback_url
        self.status_interval = bundle.status_interval

    @abstractmethod
    async def send_update(self, update: StatusUpdate) -> None:
        """Send a status update to the PA. Transport-specific."""

    @abstractmethod
    async def send_result(self, result: TaskResult) -> None:
        """Send the final task result to the PA. Transport-specific."""

    async def heartbeat(self, message: str = "", progress_pct: int | None = None, **metadata: Any) -> None:
        """Convenience: report that the agent is still working."""
        update = StatusUpdate(
            task_id=self.task_id,
            status=TaskStatus.IN_PROGRESS,
            message=message,
            progress_pct=progress_pct,
            metadata=metadata,
        )
        await self.send_update(update)

    async def blocked(self, reason: str, **metadata: Any) -> None:
        """Report that the agent is blocked on something."""
        update = StatusUpdate(
            task_id=self.task_id,
            status=TaskStatus.BLOCKED,
            message=reason,
            metadata=metadata,
        )
        await self.send_update(update)

    async def complete(self, summary: str, artifacts: list[str] | None = None, **metadata: Any) -> None:
        """Report successful completion."""
        result = TaskResult(
            task_id=self.task_id,
            success=True,
            summary=summary,
            artifacts=artifacts or [],
            metadata=metadata,
        )
        await self.send_result(result)

    async def fail(self, summary: str, errors: list[str] | None = None, **metadata: Any) -> None:
        """Report failure."""
        result = TaskResult(
            task_id=self.task_id,
            success=False,
            summary=summary,
            errors=errors or [],
            metadata=metadata,
        )
        await self.send_result(result)


class HttpStatusReporter(StatusReporter):
    """
    Reports status via HTTP POST to the PA's callback_url.

    This is the default transport. The PA runs a webhook endpoint
    that receives StatusUpdate and TaskResult payloads.
    """

    async def send_update(self, update: StatusUpdate) -> None:
        import httpx

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.callback_url}/status",
                    json=update.model_dump(mode="json"),
                    timeout=10.0,
                )
                response.raise_for_status()
            except Exception:
                logger.warning("Failed to send status update for task %s", self.task_id, exc_info=True)

    async def send_result(self, result: TaskResult) -> None:
        import httpx

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.callback_url}/result",
                    json=result.model_dump(mode="json"),
                    timeout=10.0,
                )
                response.raise_for_status()
            except Exception:
                logger.warning("Failed to send task result for task %s", self.task_id, exc_info=True)


class LogStatusReporter(StatusReporter):
    """
    Reports status via logging only. Useful for local development
    and testing without running a PA callback server.
    """

    async def send_update(self, update: StatusUpdate) -> None:
        logger.info("[%s] Status: %s — %s", self.task_id, update.status.value, update.message)

    async def send_result(self, result: TaskResult) -> None:
        level = logging.INFO if result.success else logging.ERROR
        logger.log(level, "[%s] Result: success=%s — %s", self.task_id, result.success, result.summary)


class HeartbeatRunner:
    """
    Runs periodic heartbeats in the background while an agent works.

    Usage:
        reporter = HttpStatusReporter(bundle)
        async with HeartbeatRunner(reporter):
            # Do agent work — heartbeats fire automatically
            await do_work()
    """

    def __init__(self, reporter: StatusReporter, message: str = "working") -> None:
        self._reporter = reporter
        self._message = message
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._reporter.status_interval)
            await self._reporter.heartbeat(self._message)

    async def __aenter__(self) -> HeartbeatRunner:
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
