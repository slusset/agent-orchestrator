"""
Unit tests for the PA's callback server.

The callback server is the PA's ear — it's how agents report status
and deliver results. A broken callback server means the graph never
resumes and agents scream into the void.

Tests cover:
  - HTTP layer (FastAPI routes, validation, status codes)
  - CallbackServer logic (routing, orchestrator integration, error handling)
  - Standalone mode (no orchestrator — for isolated testing)
  - Integration with OrchestratorServer mock

Traceability:
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-reports-progress.md
  Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
  Domain Model: specs/models/shared/status-update.model.yaml
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.orchestrator.callback_server import (
    CallbackServer,
    create_callback_app,
    AckResponse,
    ResultAckResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def status_update_payload():
    """A valid StatusUpdate payload as an agent would POST it."""
    return {
        "task_id": "abc123",
        "status": "in_progress",
        "message": "Running unit tests",
        "progress_pct": 60,
        "metadata": {"phase": "testing"},
    }


@pytest.fixture
def task_result_success_payload():
    """A valid successful TaskResult payload."""
    return {
        "task_id": "abc123",
        "success": True,
        "summary": "Implemented auth flow, all tests pass",
        "artifacts": ["https://github.com/org/repo/pull/42"],
        "errors": [],
        "metadata": {"files_changed": 5, "test_count": 12},
    }


@pytest.fixture
def task_result_failure_payload():
    """A valid failed TaskResult payload."""
    return {
        "task_id": "abc123",
        "success": False,
        "summary": "Tests failed after implementation",
        "artifacts": [],
        "errors": ["test_auth.py::test_login FAILED", "3 assertions failed"],
        "metadata": {},
    }


@pytest.fixture
def mock_orchestrator():
    """Mock OrchestratorServer for testing callback routing."""
    orch = MagicMock()
    orch.get_thread_for_task = MagicMock(return_value="thread-001")
    orch.handle_status_update = AsyncMock()
    orch.handle_agent_callback = AsyncMock(return_value={"tasks": []})
    orch.dispatcher = MagicMock()
    orch.dispatcher.get_running_tasks = MagicMock(return_value=["abc123"])
    orch._task_to_thread = {"abc123": "thread-001"}
    return orch


@pytest.fixture
def callback_server():
    """Standalone CallbackServer (no orchestrator)."""
    return CallbackServer()


@pytest.fixture
def callback_server_with_orch(mock_orchestrator):
    """CallbackServer wired to a mock orchestrator."""
    return CallbackServer(orchestrator_server=mock_orchestrator)


@pytest.fixture
def test_client():
    """FastAPI test client for standalone callback app."""
    app, _ = create_callback_app()
    return TestClient(app)


@pytest.fixture
def test_client_with_orch(mock_orchestrator):
    """FastAPI test client wired to mock orchestrator."""
    app, _ = create_callback_app(orchestrator_server=mock_orchestrator)
    return TestClient(app)


# ---------------------------------------------------------------------------
# CallbackServer unit tests (no HTTP)
# ---------------------------------------------------------------------------


class TestCallbackServerStandalone:
    """
    Test CallbackServer logic without an orchestrator.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
      Scenario: Agent sends periodic heartbeats
    """

    @pytest.mark.asyncio
    async def test_handle_status_no_orchestrator(
        self, callback_server, status_update_payload
    ):
        """Status updates are accepted and logged even without an orchestrator."""
        response = await callback_server.handle_status(status_update_payload)
        assert response.task_id == "abc123"
        assert response.accepted is True
        assert len(callback_server.received_updates) == 1

    @pytest.mark.asyncio
    async def test_handle_result_no_orchestrator(
        self, callback_server, task_result_success_payload
    ):
        """Results are accepted but graph is not resumed without orchestrator."""
        response = await callback_server.handle_result(task_result_success_payload)
        assert response.task_id == "abc123"
        assert response.accepted is True
        assert response.graph_resumed is False
        assert len(callback_server.received_results) == 1

    @pytest.mark.asyncio
    async def test_handle_invalid_status_payload(self, callback_server):
        """Invalid payloads are rejected with 422."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await callback_server.handle_status({"bad": "data"})
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_handle_invalid_result_payload(self, callback_server):
        """Invalid result payloads are rejected with 422."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await callback_server.handle_result({"not": "a result"})
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_clear_received(
        self, callback_server, status_update_payload, task_result_success_payload
    ):
        await callback_server.handle_status(status_update_payload)
        await callback_server.handle_result(task_result_success_payload)
        assert len(callback_server.received_updates) == 1
        assert len(callback_server.received_results) == 1

        callback_server.clear_received()
        assert len(callback_server.received_updates) == 0
        assert len(callback_server.received_results) == 0

    @pytest.mark.asyncio
    async def test_multiple_updates_accumulated(
        self, callback_server, status_update_payload
    ):
        """Multiple status updates accumulate in received_updates."""
        await callback_server.handle_status(status_update_payload)
        status_update_payload["progress_pct"] = 80
        await callback_server.handle_status(status_update_payload)
        assert len(callback_server.received_updates) == 2


# ---------------------------------------------------------------------------
# CallbackServer with orchestrator
# ---------------------------------------------------------------------------


class TestCallbackServerWithOrchestrator:
    """
    Test CallbackServer routing to OrchestratorServer.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
      Scenario: Agent reports successful completion
    """

    @pytest.mark.asyncio
    async def test_status_routed_to_orchestrator(
        self, callback_server_with_orch, mock_orchestrator, status_update_payload
    ):
        """Status updates are forwarded to orchestrator.handle_status_update."""
        await callback_server_with_orch.handle_status(status_update_payload)

        mock_orchestrator.get_thread_for_task.assert_called_once_with("abc123")
        mock_orchestrator.handle_status_update.assert_awaited_once_with(
            "thread-001", status_update_payload
        )

    @pytest.mark.asyncio
    async def test_result_resumes_graph(
        self, callback_server_with_orch, mock_orchestrator, task_result_success_payload
    ):
        """Task results trigger graph resume via handle_agent_callback."""
        response = await callback_server_with_orch.handle_result(
            task_result_success_payload
        )

        assert response.graph_resumed is True
        mock_orchestrator.handle_agent_callback.assert_awaited_once_with(
            "thread-001", task_result_success_payload
        )

    @pytest.mark.asyncio
    async def test_result_failure_still_resumes(
        self, callback_server_with_orch, mock_orchestrator, task_result_failure_payload
    ):
        """Failed results also resume the graph (graph handles failure routing)."""
        response = await callback_server_with_orch.handle_result(
            task_result_failure_payload
        )

        assert response.graph_resumed is True
        mock_orchestrator.handle_agent_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_unknown_task_logged_not_routed(
        self, callback_server_with_orch, mock_orchestrator, status_update_payload
    ):
        """Status for unknown task is logged but not forwarded."""
        mock_orchestrator.get_thread_for_task.return_value = None
        status_update_payload["task_id"] = "unknown-task"

        response = await callback_server_with_orch.handle_status(status_update_payload)
        assert response.accepted is True
        mock_orchestrator.handle_status_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_result_unknown_task_not_resumed(
        self, callback_server_with_orch, mock_orchestrator, task_result_success_payload
    ):
        """Result for unknown task is accepted but graph not resumed."""
        mock_orchestrator.get_thread_for_task.return_value = None
        task_result_success_payload["task_id"] = "unknown-task"

        response = await callback_server_with_orch.handle_result(
            task_result_success_payload
        )
        assert response.accepted is True
        assert response.graph_resumed is False
        mock_orchestrator.handle_agent_callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_graph_resume_failure_handled_gracefully(
        self, callback_server_with_orch, mock_orchestrator, task_result_success_payload
    ):
        """If graph resume fails, the result is still accepted."""
        mock_orchestrator.handle_agent_callback.side_effect = RuntimeError("graph boom")

        response = await callback_server_with_orch.handle_result(
            task_result_success_payload
        )
        assert response.accepted is True
        assert response.graph_resumed is False

    @pytest.mark.asyncio
    async def test_orchestrator_can_be_attached_later(
        self, mock_orchestrator, status_update_payload
    ):
        """Orchestrator can be set after construction."""
        server = CallbackServer()
        assert server.orchestrator is None

        server.orchestrator = mock_orchestrator
        await server.handle_status(status_update_payload)
        mock_orchestrator.handle_status_update.assert_awaited_once()


# ---------------------------------------------------------------------------
# FastAPI HTTP endpoint tests
# ---------------------------------------------------------------------------


class TestCallbackHTTPEndpoints:
    """
    Test the FastAPI routes that agents POST to.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
      The HTTP layer is what agents actually interact with.
      These tests validate the contract from the agent's perspective.
    """

    def test_post_status_accepted(self, test_client, status_update_payload):
        """POST /callback/status returns 200 with ack."""
        response = test_client.post("/callback/status", json=status_update_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "abc123"
        assert data["accepted"] is True

    def test_post_result_accepted(self, test_client, task_result_success_payload):
        """POST /callback/result returns 200 with ack."""
        response = test_client.post("/callback/result", json=task_result_success_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "abc123"
        assert data["accepted"] is True
        assert data["graph_resumed"] is False  # No orchestrator attached

    def test_post_invalid_status_422(self, test_client):
        """POST /callback/status with invalid payload returns 422."""
        response = test_client.post("/callback/status", json={"bad": "data"})
        assert response.status_code == 422

    def test_post_invalid_result_422(self, test_client):
        """POST /callback/result with invalid payload returns 422."""
        response = test_client.post("/callback/result", json={"not": "valid"})
        assert response.status_code == 422

    def test_health_endpoint(self, test_client):
        """GET /health returns ok status."""
        response = test_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_with_orchestrator(self, test_client_with_orch):
        """GET /health returns running tasks when orchestrator is attached."""
        response = test_client_with_orch.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "abc123" in data["running_tasks"]

    def test_result_resumes_graph_via_http(
        self, test_client_with_orch, task_result_success_payload
    ):
        """POST /callback/result with orchestrator resumes graph."""
        response = test_client_with_orch.post(
            "/callback/result", json=task_result_success_payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["graph_resumed"] is True


# ---------------------------------------------------------------------------
# Custom callback path tests
# ---------------------------------------------------------------------------


class TestCustomCallbackPath:
    """Test that the callback path is configurable."""

    def test_custom_path(self, status_update_payload):
        app, _ = create_callback_app(callback_path="/api/v1/agent-callback")
        client = TestClient(app)

        response = client.post(
            "/api/v1/agent-callback/status", json=status_update_payload
        )
        assert response.status_code == 200

    def test_default_path_not_available_with_custom(self, status_update_payload):
        app, _ = create_callback_app(callback_path="/api/v1/agent-callback")
        client = TestClient(app)

        response = client.post("/callback/status", json=status_update_payload)
        assert response.status_code in (404, 405)


# ---------------------------------------------------------------------------
# Integration: HttpStatusReporter → CallbackServer round-trip
# ---------------------------------------------------------------------------


class TestReporterToServerRoundTrip:
    """
    Test that HttpStatusReporter payloads are accepted by the callback server.

    This verifies the contract between the agent-side reporter and
    the PA-side callback server — the critical integration boundary.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
      Story: specs/stories/agent-lifecycle/agent-reports-progress.md
      This is the core agent↔PA communication contract.
    """

    def test_status_update_round_trip(self, test_client):
        """StatusUpdate model serializes to a payload the server accepts."""
        from src.contracts.task_bundle import StatusUpdate, TaskStatus

        update = StatusUpdate(
            task_id="round-trip-001",
            status=TaskStatus.IN_PROGRESS,
            message="Implementing feature",
            progress_pct=45,
        )
        payload = update.model_dump(mode="json")

        response = test_client.post("/callback/status", json=payload)
        assert response.status_code == 200
        assert response.json()["task_id"] == "round-trip-001"

    def test_task_result_round_trip(self, test_client):
        """TaskResult model serializes to a payload the server accepts."""
        from src.contracts.task_bundle import TaskResult

        result = TaskResult(
            task_id="round-trip-001",
            success=True,
            summary="All done",
            artifacts=["https://github.com/org/repo/pull/99"],
        )
        payload = result.model_dump(mode="json")

        response = test_client.post("/callback/result", json=payload)
        assert response.status_code == 200
        assert response.json()["task_id"] == "round-trip-001"

    def test_failed_result_round_trip(self, test_client):
        """Failed TaskResult also accepted by server."""
        from src.contracts.task_bundle import TaskResult

        result = TaskResult(
            task_id="round-trip-002",
            success=False,
            summary="Tests failed",
            errors=["test_foo.py FAILED"],
        )
        payload = result.model_dump(mode="json")

        response = test_client.post("/callback/result", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "round-trip-002"
        assert data["accepted"] is True
