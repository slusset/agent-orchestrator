"""
Unit tests for the CodingAgent.

Tests cover the agent's workflow: workspace setup, implementation plan,
test execution, PR creation, and the full lifecycle via BaseAgent.run().

Uses mocked GitWorkspace and reporter to isolate agent logic from
real git operations (those are tested in test_git_workspace.py).

Traceability:
  Persona: specs/personas/coding-agent.md
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
  Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.coding_agent import CodingAgent
from src.agents.git_workspace import GitWorkspace, PRInfo, TestResults, make_branch_name
from src.contracts.coding_bundle import CodingBundle
from src.contracts.status_reporter import LogStatusReporter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coding_bundle():
    return CodingBundle(
        task_id="code-001",
        objective="Add user authentication endpoint",
        callback_url="http://localhost:9000/callback",
        acceptance_criteria=[
            "POST /auth/login returns JWT token",
            "Invalid credentials return 401",
        ],
        repo_url="https://github.com/test/repo.git",
        base_branch="main",
        branch_prefix="feature/",
        focus_paths=["src/auth/"],
        protected_paths=[".github/"],
        run_unit_tests=True,
        test_frameworks=["pytest"],
        draft_pr=True,
    )


@pytest.fixture
def mock_reporter(coding_bundle):
    reporter = MagicMock(spec=LogStatusReporter)
    reporter.heartbeat = AsyncMock()
    reporter.complete = AsyncMock()
    reporter.fail = AsyncMock()
    reporter.task_id = coding_bundle.task_id
    reporter.callback_url = coding_bundle.callback_url
    reporter.status_interval = coding_bundle.status_interval
    return reporter


@pytest.fixture
def mock_workspace():
    """Create a mock GitWorkspace for testing agent logic without git."""
    ws = AsyncMock(spec=GitWorkspace)
    ws.clone = AsyncMock(return_value="/tmp/workspace/repo")
    ws.create_branch = AsyncMock(return_value="feature/code-001-add-user-authentication")
    ws.commit_all = AsyncMock()
    ws.push = AsyncMock()
    ws.create_pr = AsyncMock(return_value=PRInfo(
        url="https://github.com/test/repo/pull/42",
        number=42,
        title="Add user authentication endpoint",
        branch="feature/code-001-add-user-authentication",
    ))
    ws.run_tests = AsyncMock(return_value=TestResults(passed=True, count=5))
    ws.cleanup = MagicMock()
    ws.work_dir = "/tmp/workspace/repo"
    ws.branch = "feature/code-001-add-user-authentication"
    ws.base_branch = "main"
    return ws


# ---------------------------------------------------------------------------
# CodingAgent — basic properties
# ---------------------------------------------------------------------------


class TestCodingAgentProperties:

    def test_agent_type(self, coding_bundle):
        agent = CodingAgent(bundle=coding_bundle)
        assert agent.agent_type == "coding"

    def test_coding_bundle_access(self, coding_bundle):
        agent = CodingAgent(bundle=coding_bundle)
        assert agent.coding_bundle.repo_url == "https://github.com/test/repo.git"
        assert agent.coding_bundle.focus_paths == ["src/auth/"]

    def test_pr_body_generation(self, coding_bundle, mock_reporter):
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        body = agent._build_pr_body(["src/auth/login.py", "tests/test_login.py"])

        assert "Add user authentication endpoint" in body
        assert "POST /auth/login returns JWT token" in body
        assert "`src/auth/login.py`" in body
        assert "code-001" in body

    def test_pr_body_with_template(self, coding_bundle, mock_reporter):
        coding_bundle.pr_template = "Custom template: {{objective}}"
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        body = agent._build_pr_body(["file.py"])
        assert body == "Custom template: {{objective}}"


# ---------------------------------------------------------------------------
# CodingAgent — workspace setup
# ---------------------------------------------------------------------------


class TestCodingAgentWorkspace:

    @pytest.mark.asyncio
    async def test_setup_workspace_clones_and_branches(self, coding_bundle, mock_reporter, mock_workspace):
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        branch = await agent._setup_workspace(mock_workspace)

        mock_workspace.clone.assert_awaited_once()
        mock_workspace.create_branch.assert_awaited_once()
        assert branch.startswith("feature/")
        assert "code-001" in branch or "code001" in branch.replace("-", "")

    @pytest.mark.asyncio
    async def test_branch_name_from_bundle(self, coding_bundle):
        name = make_branch_name(
            prefix=coding_bundle.branch_prefix,
            task_id=coding_bundle.task_id,
            objective=coding_bundle.objective,
        )
        assert name.startswith("feature/")
        assert "code-001" in name[:20]
        assert "add-user-auth" in name


# ---------------------------------------------------------------------------
# CodingAgent — execute flow
# ---------------------------------------------------------------------------


class TestCodingAgentExecute:

    @pytest.mark.asyncio
    async def test_execute_success_flow(self, coding_bundle, mock_reporter, mock_workspace):
        """Full execute flow with passing tests."""
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)

        with patch.object(agent, '_setup_workspace', new_callable=AsyncMock) as mock_setup, \
             patch.object(agent, '_analyze_task', new_callable=AsyncMock) as mock_analyze, \
             patch.object(agent, '_implement', new_callable=AsyncMock) as mock_impl, \
             patch.object(agent, '_run_tests', new_callable=AsyncMock) as mock_tests, \
             patch.object(agent, '_create_pr', new_callable=AsyncMock) as mock_pr, \
             patch('src.agents.coding_agent.GitWorkspace') as MockWS:

            mock_ws_instance = mock_workspace
            MockWS.return_value = mock_ws_instance

            mock_setup.return_value = "feature/test-branch"
            mock_analyze.return_value = {"approach": "test", "files_to_modify": []}
            mock_impl.return_value = ["src/auth/login.py"]
            mock_tests.return_value = TestResults(passed=True, count=5)
            mock_pr.return_value = "https://github.com/test/repo/pull/42"

            result = await agent.execute()

            assert result["summary"] == "Implemented 'Add user authentication endpoint' and created PR"
            assert "https://github.com/test/repo/pull/42" in result["artifacts"]
            assert result["metadata"]["tests_passed"] is True
            assert result["metadata"]["branch"] == "feature/test-branch"

    @pytest.mark.asyncio
    async def test_execute_test_failure_triggers_fix(self, coding_bundle, mock_reporter, mock_workspace):
        """When tests fail, agent attempts one fix cycle."""
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)

        call_count = 0

        async def tests_side_effect(ws):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return TestResults(passed=False, count=5, failures=["test_login"])
            return TestResults(passed=True, count=5)

        with patch.object(agent, '_setup_workspace', new_callable=AsyncMock) as mock_setup, \
             patch.object(agent, '_analyze_task', new_callable=AsyncMock) as mock_analyze, \
             patch.object(agent, '_implement', new_callable=AsyncMock) as mock_impl, \
             patch.object(agent, '_run_tests', new_callable=AsyncMock, side_effect=tests_side_effect), \
             patch.object(agent, '_fix_test_failures', new_callable=AsyncMock) as mock_fix, \
             patch.object(agent, '_create_pr', new_callable=AsyncMock) as mock_pr, \
             patch('src.agents.coding_agent.GitWorkspace') as MockWS:

            MockWS.return_value = mock_workspace
            mock_setup.return_value = "feature/test-branch"
            mock_analyze.return_value = {"approach": "test", "files_to_modify": []}
            mock_impl.return_value = ["src/auth/login.py"]
            mock_fix.return_value = ["src/auth/login.py"]
            mock_pr.return_value = "https://github.com/test/repo/pull/42"

            result = await agent.execute()

            # Fix was called
            mock_fix.assert_awaited_once()
            # PR was still created (tests passed on retry)
            assert "pull/42" in result["artifacts"][0]

    @pytest.mark.asyncio
    async def test_execute_test_failure_after_fix_raises(self, coding_bundle, mock_reporter, mock_workspace):
        """When tests fail even after fix attempt, agent raises."""
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)

        with patch.object(agent, '_setup_workspace', new_callable=AsyncMock) as mock_setup, \
             patch.object(agent, '_analyze_task', new_callable=AsyncMock), \
             patch.object(agent, '_implement', new_callable=AsyncMock) as mock_impl, \
             patch.object(agent, '_run_tests', new_callable=AsyncMock) as mock_tests, \
             patch.object(agent, '_fix_test_failures', new_callable=AsyncMock) as mock_fix, \
             patch('src.agents.coding_agent.GitWorkspace') as MockWS:

            MockWS.return_value = mock_workspace
            mock_setup.return_value = "feature/test-branch"
            mock_impl.return_value = []
            mock_tests.return_value = TestResults(passed=False, count=5, failures=["test_login"])
            mock_fix.return_value = []

            with pytest.raises(RuntimeError, match="Tests failed after fix attempt"):
                await agent.execute()

    @pytest.mark.asyncio
    async def test_execute_skips_tests_when_disabled(self, coding_bundle, mock_reporter, mock_workspace):
        """When tests are disabled, skip test phase entirely."""
        coding_bundle.run_unit_tests = False
        coding_bundle.run_integration_tests = False
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)

        with patch.object(agent, '_setup_workspace', new_callable=AsyncMock) as mock_setup, \
             patch.object(agent, '_analyze_task', new_callable=AsyncMock), \
             patch.object(agent, '_implement', new_callable=AsyncMock) as mock_impl, \
             patch.object(agent, '_run_tests', new_callable=AsyncMock) as mock_tests, \
             patch.object(agent, '_create_pr', new_callable=AsyncMock) as mock_pr, \
             patch('src.agents.coding_agent.GitWorkspace') as MockWS:

            MockWS.return_value = mock_workspace
            mock_setup.return_value = "feature/test-branch"
            mock_impl.return_value = []
            mock_pr.return_value = "https://github.com/test/repo/pull/42"

            result = await agent.execute()

            # Tests were NOT called
            mock_tests.assert_not_awaited()
            assert result["metadata"]["tests_passed"] is True


# ---------------------------------------------------------------------------
# CodingAgent — full lifecycle via BaseAgent.run()
# ---------------------------------------------------------------------------


class TestCodingAgentLifecycle:

    @pytest.mark.asyncio
    async def test_run_success_reports_complete(self, coding_bundle, mock_reporter, mock_workspace):
        """run() should call reporter.complete() on success."""
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)

        with patch.object(agent, 'execute', new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {
                "summary": "Done",
                "artifacts": ["https://github.com/test/repo/pull/42"],
                "metadata": {"branch": "feature/test"},
            }
            await agent.run()

        mock_reporter.complete.assert_awaited_once()
        call_kwargs = mock_reporter.complete.call_args.kwargs
        assert call_kwargs["summary"] == "Done"
        assert "pull/42" in call_kwargs["artifacts"][0]

    @pytest.mark.asyncio
    async def test_run_failure_reports_fail(self, coding_bundle, mock_reporter, mock_workspace):
        """run() should call reporter.fail() on exception."""
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)

        with patch.object(agent, 'execute', new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("Clone failed")
            await agent.run()

        mock_reporter.fail.assert_awaited_once()
        call_kwargs = mock_reporter.fail.call_args.kwargs
        assert "Clone failed" in call_kwargs["summary"]


# ---------------------------------------------------------------------------
# CodingAgent — PR body builder
# ---------------------------------------------------------------------------


class TestPRBodyBuilder:

    def test_includes_acceptance_criteria_as_checklist(self, coding_bundle, mock_reporter):
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        body = agent._build_pr_body(["file.py"])
        assert "- [ ] POST /auth/login returns JWT token" in body
        assert "- [ ] Invalid credentials return 401" in body

    def test_includes_files_changed(self, coding_bundle, mock_reporter):
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        body = agent._build_pr_body(["src/auth/login.py", "tests/test_login.py"])
        assert "`src/auth/login.py`" in body
        assert "`tests/test_login.py`" in body

    def test_no_files_section_when_empty(self, coding_bundle, mock_reporter):
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        body = agent._build_pr_body([])
        assert "Files Changed" not in body

    def test_includes_task_id(self, coding_bundle, mock_reporter):
        agent = CodingAgent(bundle=coding_bundle, reporter=mock_reporter)
        body = agent._build_pr_body([])
        assert "code-001" in body
