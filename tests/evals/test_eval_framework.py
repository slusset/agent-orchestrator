"""
Tests for the eval framework itself — task loading, runner, results.

These test the eval harness, not the coding agents. They verify that:
- Tasks load correctly from YAML
- The runner properly grades pass/fail
- Results aggregate correctly
- Seed repos have the right baseline (tests should fail)

Traceability:
  This is test infrastructure, not a feature spec.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from evals.task import EvalTask, discover_tasks
from evals.runner import EvalResult, EvalRunner, EvalSuiteResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TASKS_DIR = Path(__file__).parent.parent.parent / "evals" / "tasks"


@pytest.fixture
def hello_task():
    """Load the hello-endpoint eval task."""
    task_yaml = TASKS_DIR / "add-hello-endpoint" / "task.yaml"
    if not task_yaml.exists():
        pytest.skip("Eval tasks not found")
    return EvalTask.from_yaml(task_yaml)


@pytest.fixture
def all_tasks():
    """Load all eval tasks."""
    tasks = discover_tasks(TASKS_DIR)
    if not tasks:
        pytest.skip("No eval tasks found")
    return tasks


# ---------------------------------------------------------------------------
# EvalTask loading
# ---------------------------------------------------------------------------


class TestEvalTaskLoading:

    def test_load_hello_task(self, hello_task):
        assert hello_task.task_id == "eval-hello-endpoint"
        assert hello_task.name == "Add a hello world endpoint"
        assert hello_task.difficulty == "easy"
        assert "GET /hello" in hello_task.objective

    def test_task_has_acceptance_criteria(self, hello_task):
        assert len(hello_task.acceptance_criteria) >= 2
        assert any("200" in c for c in hello_task.acceptance_criteria)

    def test_task_has_seed_repo(self, hello_task):
        assert hello_task.seed_repo.exists()
        assert (hello_task.seed_repo / "app.py").exists()
        assert (hello_task.seed_repo / "tests" / "test_hello.py").exists()

    def test_task_validates_clean(self, hello_task):
        issues = hello_task.validate()
        assert issues == [], f"Task has validation issues: {issues}"

    def test_validation_catches_missing_fields(self):
        task = EvalTask(task_id="", name="", objective="")
        issues = task.validate()
        assert len(issues) >= 2  # At least task_id and name


class TestTaskDiscovery:

    def test_discover_finds_tasks(self, all_tasks):
        assert len(all_tasks) >= 4  # We created 4 tasks

    def test_discovered_tasks_are_valid(self, all_tasks):
        for task in all_tasks:
            issues = task.validate()
            assert issues == [], f"Task {task.task_id} has issues: {issues}"

    def test_discover_nonexistent_dir(self):
        tasks = discover_tasks(Path("/nonexistent"))
        assert tasks == []

    def test_all_tasks_have_unique_ids(self, all_tasks):
        ids = [t.task_id for t in all_tasks]
        assert len(ids) == len(set(ids)), f"Duplicate task IDs: {ids}"


# ---------------------------------------------------------------------------
# Seed repo baselines — tests should FAIL before agent works
# ---------------------------------------------------------------------------


class TestSeedRepoBaselines:
    """Verify that each seed repo's tests actually fail out of the box."""

    @pytest.mark.asyncio
    async def test_hello_endpoint_baseline_fails(self, hello_task):
        """The hello endpoint seed repo tests should fail (no endpoint yet)."""
        result = await self._run_tests_in_seed_repo(hello_task)
        assert not result, "Baseline tests should FAIL but they passed"

    @pytest.mark.asyncio
    async def test_fibonacci_baseline_fails(self):
        """The fibonacci seed repo tests should fail (NotImplementedError)."""
        task_yaml = TASKS_DIR / "add-fibonacci-function" / "task.yaml"
        if not task_yaml.exists():
            pytest.skip("Task not found")
        task = EvalTask.from_yaml(task_yaml)
        result = await self._run_tests_in_seed_repo(task)
        assert not result, "Baseline tests should FAIL but they passed"

    @pytest.mark.asyncio
    async def test_import_error_baseline_fails(self):
        """The import error seed repo should fail (circular import)."""
        task_yaml = TASKS_DIR / "fix-import-error" / "task.yaml"
        if not task_yaml.exists():
            pytest.skip("Task not found")
        task = EvalTask.from_yaml(task_yaml)
        result = await self._run_tests_in_seed_repo(task)
        assert not result, "Baseline tests should FAIL but they passed"

    @pytest.mark.asyncio
    async def test_todo_crud_baseline_fails(self):
        """The TODO CRUD seed repo tests should fail (no routes)."""
        task_yaml = TASKS_DIR / "add-todo-crud" / "task.yaml"
        if not task_yaml.exists():
            pytest.skip("Task not found")
        task = EvalTask.from_yaml(task_yaml)
        result = await self._run_tests_in_seed_repo(task)
        assert not result, "Baseline tests should FAIL but they passed"

    async def _run_tests_in_seed_repo(self, task: EvalTask) -> bool:
        """Run the verify command in the seed repo. Returns True if tests pass."""
        # Copy to temp dir to avoid side effects
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "workspace"
            shutil.copytree(task.seed_repo, work_dir)

            parts = task.verify_command.split()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *parts,
                    cwd=work_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30.0)
                return proc.returncode == 0
            except Exception:
                return False


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


class TestEvalResult:

    def test_passed_result(self):
        r = EvalResult(task_id="test-1", cli_name="claude-code", passed=True)
        d = r.to_dict()
        assert d["passed"] is True
        assert d["cli_name"] == "claude-code"

    def test_failed_result(self):
        r = EvalResult(
            task_id="test-1", cli_name="codex",
            passed=False, error="Tests still failing",
        )
        d = r.to_dict()
        assert d["passed"] is False
        assert "failing" in d["error"]


# ---------------------------------------------------------------------------
# EvalSuiteResult aggregation
# ---------------------------------------------------------------------------


class TestEvalSuiteResult:

    def test_pass_rate(self):
        suite = EvalSuiteResult(results=[
            EvalResult(task_id="a", cli_name="claude-code", passed=True),
            EvalResult(task_id="b", cli_name="claude-code", passed=False),
            EvalResult(task_id="a", cli_name="codex", passed=True),
            EvalResult(task_id="b", cli_name="codex", passed=True),
        ])
        assert suite.total == 4
        assert suite.passed == 3
        assert suite.failed == 1
        assert suite.pass_rate == 75.0

    def test_by_cli(self):
        suite = EvalSuiteResult(results=[
            EvalResult(task_id="a", cli_name="claude-code", passed=True),
            EvalResult(task_id="b", cli_name="claude-code", passed=False),
            EvalResult(task_id="a", cli_name="codex", passed=True),
        ])
        grouped = suite.by_cli()
        assert len(grouped["claude-code"]) == 2
        assert len(grouped["codex"]) == 1

    def test_by_task(self):
        suite = EvalSuiteResult(results=[
            EvalResult(task_id="a", cli_name="claude-code", passed=True),
            EvalResult(task_id="a", cli_name="codex", passed=False),
        ])
        grouped = suite.by_task()
        assert len(grouped["a"]) == 2

    def test_summary_formatting(self):
        suite = EvalSuiteResult(results=[
            EvalResult(task_id="a", cli_name="claude-code", passed=True, duration_seconds=10.0),
            EvalResult(task_id="b", cli_name="claude-code", passed=False, duration_seconds=20.0),
        ])
        summary = suite.summary()
        assert "1/2 passed" in summary
        assert "50%" in summary
        assert "claude-code" in summary

    def test_empty_suite(self):
        suite = EvalSuiteResult()
        assert suite.total == 0
        assert suite.pass_rate == 0.0

    def test_to_dict(self):
        suite = EvalSuiteResult(results=[
            EvalResult(task_id="a", cli_name="x", passed=True),
        ])
        d = suite.to_dict()
        assert d["total"] == 1
        assert d["passed"] == 1
        assert len(d["results"]) == 1


# ---------------------------------------------------------------------------
# EvalRunner — with mock CLI
# ---------------------------------------------------------------------------


class TestEvalRunner:

    @pytest.mark.asyncio
    async def test_runner_with_mock_cli_that_solves_task(self, hello_task):
        """
        Test the full runner flow with a mock CLI that 'solves' the task
        by writing the correct implementation.
        """
        runner = EvalRunner(cli_types=["claude-code"])

        # Mock the CLI to write the solution
        async def mock_run(request):
            # Write the hello endpoint to app.py
            app_path = request.work_dir / "app.py"
            app_path.write_text(
                'from fastapi import FastAPI\n'
                'app = FastAPI()\n\n'
                '@app.get("/hello")\n'
                'def hello():\n'
                '    return {"message": "hello world"}\n'
            )
            from src.agents.agent_cli import CLIResponse
            return CLIResponse(success=True, output="Done", exit_code=0)

        with patch("evals.runner.create_cli") as mock_create:
            mock_cli = AsyncMock()
            mock_cli.run = mock_run
            mock_cli.name = "claude-code"
            mock_create.return_value = mock_cli

            result = await runner.run_one(hello_task, "claude-code")

        assert result.passed, f"Expected pass but got: {result.error}"
        assert result.baseline_failed  # Baseline should have failed

    @pytest.mark.asyncio
    async def test_runner_with_noop_cli_fails(self, hello_task):
        """A CLI that does nothing should result in a failed eval."""
        runner = EvalRunner(cli_types=["claude-code"])

        with patch("evals.runner.create_cli") as mock_create:
            mock_cli = AsyncMock()
            mock_cli.run = AsyncMock(return_value=type("R", (), {
                "success": True, "output": "Done", "exit_code": 0, "error": "",
            })())
            mock_cli.name = "claude-code"
            mock_create.return_value = mock_cli

            result = await runner.run_one(hello_task, "claude-code")

        assert not result.passed  # CLI did nothing, tests still fail
