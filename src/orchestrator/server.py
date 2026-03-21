"""
Orchestrator Server: Ties together the graph, dispatcher, and callback handler.

This is the runtime that:
1. Compiles the LangGraph graph with a checkpointer
2. Runs agent dispatches when the graph emits TaskRecords
3. Receives callbacks from agents and resumes the graph

For local dev, uses MemorySaver. In production, uses PostgresSaver.

The key flow:
    Stakeholder sends message
    → Graph runs: plan → dispatch → INTERRUPT
    → Dispatcher launches agent in background
    → Agent works, sends heartbeats
    → Agent completes, POSTs result to callback
    → Server receives callback, resumes graph with Command(resume=result)
    → Graph continues: evaluate → route → next dispatch or END
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt

from src.contracts.task_bundle import TaskResult, TaskStatus
from src.orchestrator.callback_handler import CallbackHandler
from src.orchestrator.dispatcher import AgentDispatcher
from src.orchestrator.graph import build_orchestrator_graph
from src.orchestrator.state import OrchestratorState, TaskRecord

logger = logging.getLogger(__name__)


class OrchestratorServer:
    """
    The runtime orchestrator that connects all the pieces.

    Usage:
        server = OrchestratorServer()

        # Start a new story
        thread_id = await server.start_story(
            message="Build user authentication",
            story={"objective": "...", "repo_url": "..."},
        )

        # When agent callback arrives (from HTTP server)
        await server.handle_agent_callback(thread_id, result_payload)
    """

    def __init__(self, transport: str = "log") -> None:
        graph_builder = build_orchestrator_graph()
        self.checkpointer = MemorySaver()
        self.graph = graph_builder.compile(checkpointer=self.checkpointer)
        self.dispatcher = AgentDispatcher(transport=transport)
        self.callback_handler = CallbackHandler()

        # Track which thread_id each task_id belongs to
        self._task_to_thread: dict[str, str] = {}

    async def start_story(
        self,
        message: str,
        story: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> str:
        """
        Kick off a new story/feature request.

        The graph will plan the work, dispatch to the coding agent,
        and then INTERRUPT waiting for the agent's callback.

        Returns the thread_id for resuming later.
        """
        thread_id = thread_id or uuid4().hex[:12]
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "tasks": [],
            "current_story": story or {},
            "context": context or {},
        }

        logger.info("Starting story on thread %s: %s", thread_id, message[:80])

        # Run the graph — it will execute until it hits the interrupt in wait_for_agent
        result = self.graph.invoke(initial_state, config)

        # After invoke returns (at interrupt), check for dispatched tasks
        await self._dispatch_pending_tasks(result, thread_id)

        return thread_id

    async def handle_agent_callback(
        self, thread_id: str, result_payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Handle a callback from an agent.

        Validates the result, then resumes the interrupted graph
        with the result data. The graph picks up at wait_for_agent,
        processes the result, evaluates it, and routes to the next step.
        """
        task_id = result_payload.get("task_id", "unknown")
        logger.info("Received callback for task %s on thread %s", task_id, thread_id)

        # Process through callback handler for validation/logging
        self.callback_handler.handle_task_result(result_payload)

        # Resume the graph with the result
        config = {"configurable": {"thread_id": thread_id}}

        result = self.graph.invoke(
            Command(resume=result_payload),
            config,
        )

        # Check if a new task was dispatched (graph may have routed to next agent)
        await self._dispatch_pending_tasks(result, thread_id)

        return result

    async def handle_status_update(
        self, thread_id: str, update_payload: dict[str, Any]
    ) -> None:
        """
        Handle a heartbeat/status update from an agent.

        Status updates don't resume the graph — they're just logged
        and stored for the watchdog to monitor.
        """
        self.callback_handler.handle_status_update(update_payload)

    async def _dispatch_pending_tasks(
        self, state: dict[str, Any], thread_id: str
    ) -> None:
        """
        Check state for newly dispatched tasks and launch agents for them.
        """
        tasks = state.get("tasks", [])
        for task in tasks:
            if task["status"] == TaskStatus.DISPATCHED.value:
                task_id = task["task_id"]
                if not self.dispatcher.is_running(task_id):
                    self._task_to_thread[task_id] = thread_id
                    await self.dispatcher.dispatch(task)

    def get_thread_for_task(self, task_id: str) -> str | None:
        """Look up which thread a task belongs to."""
        return self._task_to_thread.get(task_id)

    async def shutdown(self) -> None:
        """Graceful shutdown — cancel all running agents."""
        await self.dispatcher.cancel_all()
