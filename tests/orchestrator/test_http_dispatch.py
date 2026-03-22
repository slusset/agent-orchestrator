"""
Unit tests for HTTP agent dispatch — the PA sending TaskBundles to remote agents.

This tests the sending side of the dispatch↔callback loop:
  PA dispatcher  →  HTTP POST /execute  →  Agent Runner  →  202 Accepted

The receiving side (callback) is tested in test_callback_server.py.
Together they prove the full async communication contract.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
  Domain Model: specs/models/agent/agent.profile.yaml
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.contracts import (
    AgentCapabilityProfile,
    InvocationMethod,
    CODING_AGENT_PROFILE,
    DEFAULT_PROFILES,
)
from src.orchestrator.dispatcher import AgentDispatcher
from src.orchestrator.state import TaskRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coding_task_record():
    """A TaskRecord as the graph's dispatch node would produce."""
    return TaskRecord(
        task_id="task-http-001",
        agent_type="coding",
        bundle={
            "task_id": "task-http-001",
            "objective": "Implement hello world",
            "callback_url": "http://localhost:9000/callback",
            "repo_url": "https://github.com/org/repo",
            "base_branch": "main",
            "skills": ["bdd_specs"],
            "acceptance_criteria": ["prints hello world"],
        },
        status="dispatched",
        last_update=None,
        result=None,
        pr_url=None,
    )


@pytest.fixture
def http_coding_profile():
    """Coding agent profile configured for HTTP invocation."""
    return AgentCapabilityProfile(
        agent_type="coding",
        name="Remote Coding Agent",
        implicit_skills=CODING_AGENT_PROFILE.implicit_skills,
        configurable_skills=CODING_AGENT_PROFILE.configurable_skills,
        tools=CODING_AGENT_PROFILE.tools,
        supported_languages=CODING_AGENT_PROFILE.supported_languages,
        invocation=InvocationMethod.HTTP,
        endpoint="http://agent-host:8001",
    )


@pytest.fixture
def http_dispatcher(http_coding_profile):
    """Dispatcher with HTTP-configured coding agent."""
    return AgentDispatcher(
        profiles={"coding": http_coding_profile},
        transport="http",
    )


# ---------------------------------------------------------------------------
# _dispatch_http tests
# ---------------------------------------------------------------------------


class TestDispatchHTTP:
    """
    Test the PA dispatching TaskBundles to remote agents via HTTP.

    Traceability:
      Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
      Scenario: PA creates and dispatches a CodingBundle
    """

    @pytest.mark.asyncio
    async def test_dispatch_posts_to_agent_endpoint(
        self, http_dispatcher, coding_task_record
    ):
        """Dispatcher POSTs {agent_type, bundle} to the agent's /execute."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"task_id": "task-http-001", "status": "accepted"}
        mock_response.raise_for_status = MagicMock()

        with patch("src.orchestrator.dispatcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await http_dispatcher.dispatch(coding_task_record)

            mock_client.post.assert_awaited_once()
            call_args = mock_client.post.call_args

            # Verify URL
            assert call_args[0][0] == "http://agent-host:8001/execute"

            # Verify payload structure
            payload = call_args[1]["json"]
            assert payload["agent_type"] == "coding"
            assert payload["bundle"]["task_id"] == "task-http-001"
            assert payload["bundle"]["objective"] == "Implement hello world"
            assert payload["bundle"]["callback_url"] == "http://localhost:9000/callback"

    @pytest.mark.asyncio
    async def test_dispatch_http_no_endpoint_logs_error(self, coding_task_record):
        """HTTP profile without endpoint configured should log error, not crash."""
        no_endpoint_profile = AgentCapabilityProfile(
            agent_type="coding",
            name="Broken Agent",
            invocation=InvocationMethod.HTTP,
            endpoint=None,  # Missing!
        )
        dispatcher = AgentDispatcher(profiles={"coding": no_endpoint_profile})

        # Should not raise — just logs error
        await dispatcher.dispatch(coding_task_record)

    @pytest.mark.asyncio
    async def test_dispatch_http_network_failure_handled(
        self, http_dispatcher, coding_task_record
    ):
        """Network failures during dispatch are caught and logged."""
        with patch("src.orchestrator.dispatcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Should not raise
            await http_dispatcher.dispatch(coding_task_record)

    @pytest.mark.asyncio
    async def test_dispatch_http_timeout_handled(
        self, http_dispatcher, coding_task_record
    ):
        """HTTP timeout during dispatch is caught and logged."""
        import httpx

        with patch("src.orchestrator.dispatcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.TimeoutException("timed out")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await http_dispatcher.dispatch(coding_task_record)

    @pytest.mark.asyncio
    async def test_dispatch_http_server_error_handled(
        self, http_dispatcher, coding_task_record
    ):
        """HTTP 500 from agent is caught via raise_for_status."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_response
            )
        )

        with patch("src.orchestrator.dispatcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await http_dispatcher.dispatch(coding_task_record)

    @pytest.mark.asyncio
    async def test_dispatch_unknown_agent_logs_error(self):
        """Dispatching to an unregistered agent type logs error."""
        dispatcher = AgentDispatcher(profiles={})
        record = TaskRecord(
            task_id="x",
            agent_type="unknown",
            bundle={},
            status="dispatched",
            last_update=None,
            result=None,
            pr_url=None,
        )
        # Should not raise
        await dispatcher.dispatch(record)


# ---------------------------------------------------------------------------
# Invocation method routing tests
# ---------------------------------------------------------------------------


class TestInvocationRouting:
    """
    Test that the dispatcher routes to the correct invocation method.

    Traceability:
      Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
      Scenario: PA consults agent capability profile before dispatch
    """

    @pytest.mark.asyncio
    async def test_local_profile_uses_local_dispatch(self, coding_task_record):
        """LOCAL invocation dispatches via asyncio task."""
        dispatcher = AgentDispatcher(transport="log")
        # Default profiles are LOCAL

        with patch.object(dispatcher, "_dispatch_local", new_callable=AsyncMock) as mock_local:
            await dispatcher.dispatch(coding_task_record)
            mock_local.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_http_profile_uses_http_dispatch(
        self, http_dispatcher, coding_task_record
    ):
        """HTTP invocation dispatches via HTTP POST."""
        with patch.object(http_dispatcher, "_dispatch_http", new_callable=AsyncMock) as mock_http:
            await http_dispatcher.dispatch(coding_task_record)
            mock_http.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_queue_invocation_not_implemented(self, coding_task_record):
        """QUEUE invocation logs error (not yet implemented)."""
        queue_profile = AgentCapabilityProfile(
            agent_type="coding",
            name="Queue Agent",
            invocation=InvocationMethod.QUEUE,
        )
        dispatcher = AgentDispatcher(profiles={"coding": queue_profile})

        # Should not raise — just logs
        await dispatcher.dispatch(coding_task_record)


# ---------------------------------------------------------------------------
# Agent runner HTTP endpoint tests (agent side)
# ---------------------------------------------------------------------------


class TestAgentRunnerEndpoints:
    """
    Test the agent runner's FastAPI endpoints that receive dispatches.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
      Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
    """

    def test_execute_accepts_valid_bundle(self):
        """POST /execute with valid bundle returns 200 with task_id."""
        from fastapi.testclient import TestClient
        from src.agents.runner import create_agent_app

        app = create_agent_app("coding", transport="log")
        client = TestClient(app)

        response = client.post("/execute", json={
            "agent_type": "coding",
            "bundle": {
                "objective": "Test task",
                "callback_url": "http://localhost:9000/callback",
                "repo_url": "https://github.com/org/repo",
            },
        })
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "accepted"

    def test_execute_rejects_wrong_agent_type(self):
        """POST /execute with wrong agent_type returns 400."""
        from fastapi.testclient import TestClient
        from src.agents.runner import create_agent_app

        app = create_agent_app("coding", transport="log")
        client = TestClient(app)

        response = client.post("/execute", json={
            "agent_type": "uat",
            "bundle": {
                "objective": "Test",
                "callback_url": "http://x",
                "repo_url": "http://x",
            },
        })
        assert response.status_code == 400
        assert "serves coding" in response.json()["detail"]

    def test_status_endpoint(self):
        """GET /status returns agent runner status."""
        from fastapi.testclient import TestClient
        from src.agents.runner import create_agent_app

        app = create_agent_app("coding", transport="log")
        client = TestClient(app)

        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["agent_type"] == "coding"
        assert data["available"] is True
        assert data["max_concurrent"] == 1

    def test_cancel_nonexistent_task(self):
        """POST /cancel/{task_id} for unknown task returns cancelled=False."""
        from fastapi.testclient import TestClient
        from src.agents.runner import create_agent_app

        app = create_agent_app("coding", transport="log")
        client = TestClient(app)

        response = client.post("/cancel/nonexistent-task")
        assert response.status_code == 200
        assert response.json()["cancelled"] is False


# ---------------------------------------------------------------------------
# Dispatch payload contract tests
# ---------------------------------------------------------------------------


class TestDispatchPayloadContract:
    """
    Verify that the dispatcher's HTTP payload matches what the agent runner expects.

    This is the critical integration boundary — if these diverge, dispatch fails.

    Traceability:
      Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
      The payload is the over-the-wire representation of the TaskBundle contract.
    """

    def test_payload_matches_execute_request_schema(self, coding_task_record):
        """The payload _dispatch_http builds matches ExecuteRequest."""
        from src.agents.runner import ExecuteRequest

        # This is exactly what _dispatch_http sends
        payload = {
            "agent_type": coding_task_record["agent_type"],
            "bundle": coding_task_record["bundle"],
        }

        # Must parse without error
        request = ExecuteRequest.model_validate(payload)
        assert request.agent_type == "coding"
        assert request.bundle["objective"] == "Implement hello world"

    def test_bundle_in_payload_deserializes_to_coding_bundle(self, coding_task_record):
        """The bundle dict in the payload can be deserialized to CodingBundle."""
        from src.contracts.coding_bundle import CodingBundle

        bundle = CodingBundle.model_validate(coding_task_record["bundle"])
        assert bundle.task_id == "task-http-001"
        assert bundle.repo_url == "https://github.com/org/repo"
        assert bundle.callback_url == "http://localhost:9000/callback"


# ---------------------------------------------------------------------------
# Remote agent monitoring tests
# ---------------------------------------------------------------------------


class TestRemoteAgentMonitoring:
    """
    Test check_remote_agent() for monitoring remote agent runners.

    Traceability:
      Feature: specs/features/monitoring/pa-detects-timeout.feature
    """

    @pytest.mark.asyncio
    async def test_check_remote_agent_success(self, http_dispatcher):
        """Successfully checks remote agent status."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agent_type": "coding",
            "running_tasks": ["task-001"],
            "max_concurrent": 1,
            "available": False,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.orchestrator.dispatcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            status = await http_dispatcher.check_remote_agent("coding")

            assert status is not None
            assert status["agent_type"] == "coding"
            assert "task-001" in status["running_tasks"]

    @pytest.mark.asyncio
    async def test_check_remote_agent_failure_returns_none(self, http_dispatcher):
        """Network failure returns None instead of raising."""
        with patch("src.orchestrator.dispatcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            status = await http_dispatcher.check_remote_agent("coding")
            assert status is None

    @pytest.mark.asyncio
    async def test_check_unknown_agent_returns_none(self, http_dispatcher):
        """Checking an unregistered agent type returns None."""
        status = await http_dispatcher.check_remote_agent("nonexistent")
        assert status is None

    @pytest.mark.asyncio
    async def test_check_local_agent_no_endpoint_returns_none(self):
        """LOCAL agents have no endpoint — returns None."""
        dispatcher = AgentDispatcher()  # Default LOCAL profiles
        status = await dispatcher.check_remote_agent("coding")
        assert status is None
