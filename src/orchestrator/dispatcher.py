"""
Agent Dispatcher: Invokes agents based on their capability profiles.

The dispatcher consults the AgentCapabilityProfile to decide HOW
to invoke each agent:
    LOCAL → asyncio.create_task(agent.run())
    HTTP  → POST /execute to the agent's endpoint
    QUEUE → (future) push to message queue

The dispatcher is the bridge between the graph's dispatch nodes
and the actual agent processes. It also provides the watchdog
with a way to check on running agents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.agents.base import BaseAgent
from src.agents.coding_agent import CodingAgent
from src.contracts.capability_profile import (
    AgentCapabilityProfile,
    DEFAULT_PROFILES,
    InvocationMethod,
)
from src.contracts.coding_bundle import CodingBundle
from src.contracts.status_reporter import HttpStatusReporter, LogStatusReporter, StatusReporter
from src.contracts.task_bundle import TaskBundle
from src.orchestrator.state import TaskRecord

logger = logging.getLogger(__name__)


# Local agent registry — agent_type → (bundle_class, agent_class)
LOCAL_AGENT_REGISTRY: dict[str, tuple[type[TaskBundle], type[BaseAgent]]] = {
    "coding": (CodingBundle, CodingAgent),
    # "uat": (UATBundle, UATAgent),
    # "devops": (DevOpsBundle, DevOpsAgent),
    # "pr": (PRBundle, PRAgent),
}


class AgentDispatcher:
    """
    Invokes agents using capability profiles to determine method.

    The dispatcher maintains:
    - profiles: what each agent can do and how to reach it
    - running: tracking of active local agent tasks
    - transport: default status reporter transport
    """

    def __init__(
        self,
        profiles: dict[str, AgentCapabilityProfile] | None = None,
        transport: str = "log",
    ) -> None:
        self.profiles = profiles or dict(DEFAULT_PROFILES)
        self.transport = transport
        self._running: dict[str, asyncio.Task] = {}

    def register_profile(self, profile: AgentCapabilityProfile) -> None:
        """Register or update an agent's capability profile."""
        self.profiles[profile.agent_type] = profile
        logger.info(
            "Registered profile: %s (%s via %s)",
            profile.name,
            profile.agent_type,
            profile.invocation.value,
        )

    def get_profile(self, agent_type: str) -> AgentCapabilityProfile | None:
        """Get a registered agent profile."""
        return self.profiles.get(agent_type)

    def _make_reporter(self, bundle: TaskBundle) -> StatusReporter:
        """Create the appropriate StatusReporter."""
        if self.transport == "http":
            return HttpStatusReporter(bundle)
        return LogStatusReporter(bundle)

    async def dispatch(self, task_record: TaskRecord) -> None:
        """
        Dispatch a task to the appropriate agent.

        Consults the capability profile to determine invocation method:
        - LOCAL: create agent in-process, run as asyncio task
        - HTTP: POST the bundle to the agent's endpoint
        - QUEUE: (future) push to message queue
        """
        agent_type = task_record["agent_type"]
        task_id = task_record["task_id"]

        profile = self.profiles.get(agent_type)
        if not profile:
            logger.error("No profile registered for agent type: %s", agent_type)
            return

        if profile.invocation == InvocationMethod.LOCAL:
            await self._dispatch_local(task_record, profile)
        elif profile.invocation == InvocationMethod.HTTP:
            await self._dispatch_http(task_record, profile)
        elif profile.invocation == InvocationMethod.QUEUE:
            logger.error("Queue invocation not yet implemented")
        else:
            logger.error("Unknown invocation method: %s", profile.invocation)

    async def _dispatch_local(
        self,
        task_record: TaskRecord,
        profile: AgentCapabilityProfile,
    ) -> None:
        """Invoke agent as a local asyncio task."""
        agent_type = task_record["agent_type"]
        task_id = task_record["task_id"]

        if agent_type not in LOCAL_AGENT_REGISTRY:
            logger.error("No local agent implementation for: %s", agent_type)
            return

        bundle_class, agent_class = LOCAL_AGENT_REGISTRY[agent_type]
        bundle = bundle_class.model_validate(task_record["bundle"])

        reporter = self._make_reporter(bundle)
        agent = agent_class(bundle=bundle, reporter=reporter)

        task = asyncio.create_task(
            self._run_local_agent(agent),
            name=f"agent-{agent_type}-{task_id}",
        )
        self._running[task_id] = task

        logger.info(
            "Dispatched %s agent locally for task %s",
            agent_type,
            task_id,
        )

    async def _dispatch_http(
        self,
        task_record: TaskRecord,
        profile: AgentCapabilityProfile,
    ) -> None:
        """Invoke agent via HTTP POST to its endpoint."""
        if not profile.endpoint:
            logger.error(
                "HTTP invocation for %s but no endpoint configured",
                profile.agent_type,
            )
            return

        payload = {
            "agent_type": task_record["agent_type"],
            "bundle": task_record["bundle"],
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{profile.endpoint}/execute",
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()
                result = response.json()

                logger.info(
                    "Dispatched %s agent via HTTP to %s — task %s accepted",
                    profile.agent_type,
                    profile.endpoint,
                    result.get("task_id", "?"),
                )

        except Exception:
            logger.error(
                "Failed to dispatch %s agent via HTTP to %s",
                profile.agent_type,
                profile.endpoint,
                exc_info=True,
            )

    async def _run_local_agent(self, agent: BaseAgent) -> None:
        """Run a local agent and clean up tracking."""
        try:
            await agent.run()
        finally:
            self._running.pop(agent.bundle.task_id, None)

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def is_running(self, task_id: str) -> bool:
        """Check if an agent is running locally."""
        task = self._running.get(task_id)
        return task is not None and not task.done()

    def get_running_tasks(self) -> list[str]:
        """Get list of locally running task IDs."""
        return [tid for tid, task in self._running.items() if not task.done()]

    async def check_remote_agent(self, agent_type: str) -> dict[str, Any] | None:
        """Check the status of a remote agent runner."""
        profile = self.profiles.get(agent_type)
        if not profile or not profile.endpoint:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{profile.endpoint}/status",
                    timeout=5.0,
                )
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.warning(
                "Failed to check remote agent %s at %s",
                agent_type,
                profile.endpoint,
                exc_info=True,
            )
            return None

    async def cancel(self, task_id: str) -> bool:
        """Cancel a locally running agent."""
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
        """Cancel all locally running agents."""
        for task_id in list(self._running.keys()):
            await self.cancel(task_id)

    # ------------------------------------------------------------------
    # Profile-aware planning helpers
    # ------------------------------------------------------------------

    def get_skills_to_include(
        self,
        agent_type: str,
        desired_skills: list[str],
    ) -> list[str]:
        """
        Given skills the PA wants an agent to use, return only
        the ones that need to be explicitly included in the TaskBundle.

        Filters out implicit skills — the agent already knows those.
        """
        profile = self.profiles.get(agent_type)
        if not profile:
            return desired_skills  # No profile, include everything

        return [
            skill for skill in desired_skills
            if profile.needs_explicit_skill(skill)
        ]

    def can_handle(self, agent_type: str, requirements: dict[str, Any]) -> bool:
        """
        Check if an agent can handle a set of requirements.

        Requirements can specify:
            language: "python"
            framework: "nextjs"
            skills: ["bdd_specs", "docker_build"]
            tools: ["playwright"]
        """
        profile = self.profiles.get(agent_type)
        if not profile:
            return False

        if "language" in requirements:
            if not profile.supports_language(requirements["language"]):
                return False

        if "skills" in requirements:
            for skill in requirements["skills"]:
                if not profile.has_skill(skill):
                    return False

        if "tools" in requirements:
            for tool in requirements["tools"]:
                if not profile.has_tool(tool):
                    return False

        return True
