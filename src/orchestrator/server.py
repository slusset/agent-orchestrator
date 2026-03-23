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
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt

from src.contracts.credentials import (
    CredentialManifest,
    CredentialProvider,
    EnvCredentialProvider,
    ResolutionResult,
    load_credential_manifest,
    validate_credentials,
)
from src.contracts.task_bundle import TaskResult, TaskStatus
from src.orchestrator.callback_handler import CallbackHandler
from src.orchestrator.dispatcher import AgentDispatcher
from src.orchestrator.graph import build_orchestrator_graph
from src.orchestrator.state import OrchestratorState, TaskRecord

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = ".pm/credentials.yaml"


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

    def __init__(
        self,
        transport: str = "log",
        credential_provider: CredentialProvider | None = None,
        manifest_path: str | Path | None = None,
    ) -> None:
        graph_builder = build_orchestrator_graph()
        self.checkpointer = MemorySaver()
        self.graph = graph_builder.compile(checkpointer=self.checkpointer)
        self.dispatcher = AgentDispatcher(transport=transport)
        self.callback_handler = CallbackHandler()

        # Credential resolution
        self.credential_provider = credential_provider or EnvCredentialProvider()
        self._manifest: CredentialManifest | None = None
        self._resolved: ResolutionResult | None = None
        self._manifest_path = Path(manifest_path) if manifest_path else Path(DEFAULT_MANIFEST_PATH)

        # Agent rules — project-level instructions injected into every bundle
        self._agent_rules: str = ""

        # Track which thread_id each task_id belongs to
        self._task_to_thread: dict[str, str] = {}

    async def boot(self) -> ResolutionResult | None:
        """
        Boot-time initialization: load credentials and agent rules.

        Call this after construction, before accepting work.
        Returns the ResolutionResult, or None if no manifest found.
        """
        # Load agent rules (project-level instructions for all agents)
        rules_path = self._manifest_path.parent / "agent-rules.md"
        if rules_path.exists():
            self._agent_rules = rules_path.read_text()
            logger.info("Loaded agent rules from %s", rules_path)
        else:
            logger.debug("No agent rules at %s", rules_path)

        try:
            self._manifest = load_credential_manifest(self._manifest_path)
        except FileNotFoundError:
            logger.info("No credential manifest at %s — skipping validation", self._manifest_path)
            return None

        self._resolved = await validate_credentials(
            self._manifest, self.credential_provider
        )

        if not self._resolved.ok:
            logger.warning(
                "Boot credential check: %s — some agent dispatches may fail",
                self._resolved.summary(),
            )

        return self._resolved

    def get_resolved_env(self, role: str | None = None) -> dict[str, str]:
        """
        Get resolved credentials as an env dict, optionally filtered by role.

        If boot() hasn't been called or no manifest exists, returns empty dict.
        """
        if self._resolved is None:
            return {}

        if role is None or self._manifest is None:
            return self._resolved.as_env()

        # Filter to only credentials relevant to this role
        role_cred_names = {
            spec.name for spec in self._manifest.for_role(role)
        }
        return {
            name: cred.value
            for name, cred in self._resolved.resolved.items()
            if name in role_cred_names
        }

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

        # Inject per-role resolved credentials into context for dispatch nodes.
        # Stored as plain dicts so they survive LangGraph serialization.
        effective_context = dict(context or {})
        if self._resolved and self._manifest:
            role_envs: dict[str, dict[str, str]] = {}
            for role in ("coding", "pr", "uat", "devops"):
                role_envs[role] = self.get_resolved_env(role)
            effective_context["_resolved_credentials"] = role_envs

        # Inject agent rules into story context so dispatch nodes include
        # them in the bundle. Agents receive these as part of the context
        # field — project-level hygiene rules, code style, testing conventions.
        effective_story = dict(story or {})
        if self._agent_rules:
            existing_context = effective_story.get("context", "")
            effective_story["context"] = (
                f"{existing_context}\n\n{self._agent_rules}".strip()
                if existing_context
                else self._agent_rules
            )

        initial_state: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "tasks": [],
            "current_story": effective_story,
            "context": effective_context,
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

        Re-injects resolved credentials into the task record before dispatch.
        Credentials are stripped during bundle serialization (exclude=True for
        checkpoint safety) so we restore them here from the server's cache.
        """
        tasks = state.get("tasks", [])
        for task in tasks:
            if task["status"] == TaskStatus.DISPATCHED.value:
                task_id = task["task_id"]
                if not self.dispatcher.is_running(task_id):
                    self._task_to_thread[task_id] = thread_id
                    # Re-inject credentials stripped during serialization.
                    # The graph stores bundle.model_dump(mode="json") which
                    # excludes resolved_env. We restore from the server's
                    # cached resolution so the dispatcher can pass them
                    # to locally-invoked agents.
                    role = task.get("agent_type", "")
                    resolved = self.get_resolved_env(role)
                    if resolved:
                        task["_resolved_env"] = resolved
                    await self.dispatcher.dispatch(task)

    def get_thread_for_task(self, task_id: str) -> str | None:
        """Look up which thread a task belongs to."""
        return self._task_to_thread.get(task_id)

    async def shutdown(self) -> None:
        """Graceful shutdown — cancel all running agents."""
        await self.dispatcher.cancel_all()
