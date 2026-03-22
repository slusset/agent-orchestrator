"""
Callback Server: FastAPI HTTP endpoint the PA runs to receive agent callbacks.

Agents POST StatusUpdate and TaskResult payloads to this server.
The server routes them through the OrchestratorServer, which:
  - For status updates: logs and stores for watchdog monitoring
  - For task results: resumes the interrupted LangGraph via Command(resume=result)

URL scheme (matches HttpStatusReporter):
  POST {callback_url}/status   →  heartbeat / progress update
  POST {callback_url}/result   →  completion / failure (resumes graph)

The callback_url in each TaskBundle is set to point at this server,
e.g., "http://localhost:9000/callback" so the agent POSTs to:
  - http://localhost:9000/callback/status
  - http://localhost:9000/callback/result
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.contracts.task_bundle import StatusUpdate, TaskResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AckResponse(BaseModel):
    """Simple acknowledgment for status updates."""

    task_id: str
    accepted: bool = True
    message: str = ""


class ResultAckResponse(BaseModel):
    """Acknowledgment for task results, with graph resume status."""

    task_id: str
    accepted: bool = True
    graph_resumed: bool = False
    message: str = ""


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    running_tasks: list[str] = Field(default_factory=list)
    pending_threads: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Callback Server
# ---------------------------------------------------------------------------


class CallbackServer:
    """
    The PA's callback HTTP server.

    Wraps an OrchestratorServer instance and exposes FastAPI routes
    for agents to POST status updates and results.

    Decoupled from OrchestratorServer so the HTTP layer can be tested
    independently (with a mock orchestrator) and swapped for other
    transports later.
    """

    def __init__(self, orchestrator_server: Any = None) -> None:
        """
        Initialize with an OrchestratorServer instance.

        If None, the server accepts and logs callbacks but cannot
        resume the graph — useful for testing the HTTP layer alone.
        """
        self._orchestrator = orchestrator_server
        self._received_updates: list[dict[str, Any]] = []
        self._received_results: list[dict[str, Any]] = []

    @property
    def orchestrator(self) -> Any:
        return self._orchestrator

    @orchestrator.setter
    def orchestrator(self, value: Any) -> None:
        self._orchestrator = value

    async def handle_status(self, payload: dict[str, Any]) -> AckResponse:
        """
        Process a StatusUpdate from an agent.

        Does NOT resume the graph — status updates are informational.
        The watchdog monitors these to detect stalled agents.
        """
        # Validate the payload
        try:
            update = StatusUpdate.model_validate(payload)
        except Exception as e:
            logger.warning("Invalid status update payload: %s", e)
            raise HTTPException(status_code=422, detail=f"Invalid payload: {e}")

        task_id = update.task_id
        self._received_updates.append(payload)
        logger.info(
            "[callback] Status update for %s: %s — %s",
            task_id,
            update.status.value,
            update.message,
        )

        # Forward to orchestrator if available
        if self._orchestrator:
            thread_id = self._orchestrator.get_thread_for_task(task_id)
            if thread_id:
                await self._orchestrator.handle_status_update(thread_id, payload)
            else:
                logger.warning(
                    "[callback] No thread found for task %s — update logged but not routed",
                    task_id,
                )

        return AckResponse(
            task_id=task_id,
            message=f"Status update received: {update.status.value}",
        )

    async def handle_result(self, payload: dict[str, Any]) -> ResultAckResponse:
        """
        Process a TaskResult from an agent.

        This is the critical path — a result means the agent is done,
        and the graph should resume with the result data.
        """
        # Validate the payload
        try:
            result = TaskResult.model_validate(payload)
        except Exception as e:
            logger.warning("Invalid task result payload: %s", e)
            raise HTTPException(status_code=422, detail=f"Invalid payload: {e}")

        task_id = result.task_id
        self._received_results.append(payload)
        logger.info(
            "[callback] Task result for %s: success=%s — %s",
            task_id,
            result.success,
            result.summary,
        )

        graph_resumed = False

        if self._orchestrator:
            thread_id = self._orchestrator.get_thread_for_task(task_id)
            if thread_id:
                try:
                    await self._orchestrator.handle_agent_callback(
                        thread_id, payload
                    )
                    graph_resumed = True
                    logger.info(
                        "[callback] Graph resumed for task %s on thread %s",
                        task_id,
                        thread_id,
                    )
                except Exception:
                    logger.error(
                        "[callback] Failed to resume graph for task %s",
                        task_id,
                        exc_info=True,
                    )
            else:
                logger.warning(
                    "[callback] No thread found for task %s — result logged but graph not resumed",
                    task_id,
                )

        return ResultAckResponse(
            task_id=task_id,
            graph_resumed=graph_resumed,
            message="Result received" + (" and graph resumed" if graph_resumed else ""),
        )

    # ------------------------------------------------------------------
    # Introspection (for tests and monitoring)
    # ------------------------------------------------------------------

    @property
    def received_updates(self) -> list[dict[str, Any]]:
        """All status updates received (for testing/debugging)."""
        return self._received_updates

    @property
    def received_results(self) -> list[dict[str, Any]]:
        """All task results received (for testing/debugging)."""
        return self._received_results

    def clear_received(self) -> None:
        """Clear received updates and results."""
        self._received_updates.clear()
        self._received_results.clear()


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_callback_app(
    orchestrator_server: Any = None,
    callback_path: str = "/callback",
) -> tuple[FastAPI, CallbackServer]:
    """
    Create a FastAPI app for the PA's callback server.

    Args:
        orchestrator_server: OrchestratorServer instance (or None for testing)
        callback_path: Base path for callback routes (default: /callback)

    Returns:
        Tuple of (FastAPI app, CallbackServer instance)

    The returned CallbackServer can be used to:
        - Attach an orchestrator later: server.orchestrator = my_server
        - Inspect received callbacks: server.received_updates

    Run with:
        app, callback_server = create_callback_app(orchestrator)
        uvicorn.run(app, host="0.0.0.0", port=9000)

    Agents should set callback_url to: http://<host>:9000/callback
    Which makes the agent POST to:
        http://<host>:9000/callback/status
        http://<host>:9000/callback/result
    """
    app = FastAPI(
        title="PA Callback Server",
        description="Receives status updates and results from agents",
    )
    callback = CallbackServer(orchestrator_server)

    @app.post(f"{callback_path}/status", response_model=AckResponse)
    async def status_endpoint(payload: dict[str, Any]) -> AckResponse:
        """Receive a StatusUpdate from an agent."""
        return await callback.handle_status(payload)

    @app.post(f"{callback_path}/result", response_model=ResultAckResponse)
    async def result_endpoint(payload: dict[str, Any]) -> ResultAckResponse:
        """Receive a TaskResult from an agent."""
        return await callback.handle_result(payload)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check endpoint."""
        running = []
        threads = []
        if callback.orchestrator:
            running = callback.orchestrator.dispatcher.get_running_tasks()
            threads = list(callback.orchestrator._task_to_thread.values())

        return HealthResponse(
            status="ok",
            running_tasks=running,
            pending_threads=threads,
        )

    return app, callback
