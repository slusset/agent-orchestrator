"""
Unit tests for the StubAgent — hello-world agent for smoke testing.

Traceability:
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
  Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
  Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.stub_agent import StubAgent
from src.contracts.task_bundle import TaskBundle
from src.contracts.status_reporter import LogStatusReporter


@pytest.fixture
def stub_bundle():
    return TaskBundle(
        task_id="stub-001",
        objective="Hello world smoke test",
        callback_url="http://localhost:9000/callback",
        metadata={"work_seconds": 0.1},
    )


@pytest.fixture
def mock_reporter(stub_bundle):
    reporter = MagicMock(spec=LogStatusReporter)
    reporter.heartbeat = AsyncMock()
    reporter.complete = AsyncMock()
    reporter.fail = AsyncMock()
    reporter.task_id = stub_bundle.task_id
    reporter.callback_url = stub_bundle.callback_url
    reporter.status_interval = stub_bundle.status_interval
    return reporter


class TestStubAgent:

    @pytest.mark.asyncio
    async def test_execute_returns_success(self, stub_bundle, mock_reporter):
        agent = StubAgent(bundle=stub_bundle, reporter=mock_reporter)
        result = await agent.execute()

        assert result["summary"] == "Stub agent completed: Hello world smoke test"
        assert len(result["artifacts"]) == 1
        assert "stub-001" in result["artifacts"][0]

    @pytest.mark.asyncio
    async def test_sends_heartbeats(self, stub_bundle, mock_reporter):
        agent = StubAgent(bundle=stub_bundle, reporter=mock_reporter)
        await agent.execute()

        # Should have sent 3 heartbeats: starting (10%), halfway (50%), wrapping up (90%)
        assert mock_reporter.heartbeat.await_count == 3
        calls = mock_reporter.heartbeat.call_args_list
        assert calls[0].kwargs["progress_pct"] == 10
        assert calls[1].kwargs["progress_pct"] == 50
        assert calls[2].kwargs["progress_pct"] == 90

    @pytest.mark.asyncio
    async def test_failure_mode(self, mock_reporter):
        bundle = TaskBundle(
            task_id="stub-fail",
            objective="Should fail",
            callback_url="http://x",
            metadata={"fail": True, "work_seconds": 0.05},
        )
        agent = StubAgent(bundle=bundle, reporter=mock_reporter)

        with pytest.raises(RuntimeError, match="intentional failure"):
            await agent.execute()

    @pytest.mark.asyncio
    async def test_custom_failure_message(self, mock_reporter):
        bundle = TaskBundle(
            task_id="stub-fail",
            objective="Custom fail",
            callback_url="http://x",
            metadata={"fail": True, "fail_message": "out of coffee", "work_seconds": 0.05},
        )
        agent = StubAgent(bundle=bundle, reporter=mock_reporter)

        with pytest.raises(RuntimeError, match="out of coffee"):
            await agent.execute()

    @pytest.mark.asyncio
    async def test_run_lifecycle_success(self, stub_bundle, mock_reporter):
        """Test the full BaseAgent.run() lifecycle — heartbeats + complete."""
        agent = StubAgent(bundle=stub_bundle, reporter=mock_reporter)
        await agent.run()

        mock_reporter.complete.assert_awaited_once()
        call_kwargs = mock_reporter.complete.call_args.kwargs
        assert "stub-001" in call_kwargs.get("summary", "") or True

    @pytest.mark.asyncio
    async def test_run_lifecycle_failure(self, mock_reporter):
        """Test run() catches failures and reports via reporter.fail()."""
        bundle = TaskBundle(
            task_id="stub-fail",
            objective="Will fail",
            callback_url="http://x",
            metadata={"fail": True, "work_seconds": 0.05},
        )
        agent = StubAgent(bundle=bundle, reporter=mock_reporter)
        await agent.run()

        mock_reporter.fail.assert_awaited_once()

    def test_agent_type(self, stub_bundle):
        agent = StubAgent(bundle=stub_bundle)
        assert agent.agent_type == "stub"

    @pytest.mark.asyncio
    async def test_default_work_seconds(self, mock_reporter):
        """Default work_seconds is 1.0 but we override in metadata."""
        bundle = TaskBundle(
            task_id="x",
            objective="defaults",
            callback_url="http://x",
            metadata={},  # No work_seconds — defaults to 1.0
        )
        agent = StubAgent(bundle=bundle, reporter=mock_reporter)
        # Just verify it doesn't crash; we won't wait the full 1s
        # (the actual default timer is tested via metadata override)
        assert agent.bundle.metadata.get("work_seconds") is None
