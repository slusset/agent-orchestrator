"""
Eval Runner: Executes eval tasks through the CodingAgent framework.

For each task × CLI combination:
    1. Copy the seed repo to a temp workspace
    2. Initialize git (if needed)
    3. Run the verify command to confirm tests FAIL (baseline)
    4. Invoke the CodingAgent with the configured CLI
    5. Run the verify command to check if tests PASS (grading)
    6. Record the result

The runner doesn't use the full orchestrator dispatch loop (no callback
server, no HTTP). It directly instantiates CodingAgent with a mock
reporter. This isolates the eval to just: CLI agent + git workspace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.task import EvalTask
from src.agents.agent_cli import AgentCLI, CLIRequest, create_cli
from src.agents.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """Result of running one task with one CLI."""

    task_id: str
    cli_name: str

    # Grading
    passed: bool = False
    baseline_failed: bool = True  # Tests failed before agent (expected)

    # Details
    files_changed: list[str] = field(default_factory=list)
    test_output: str = ""
    agent_output: str = ""
    error: str = ""

    # Timing
    duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""

    # Metadata
    exit_code: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "task_id": self.task_id,
            "cli_name": self.cli_name,
            "passed": self.passed,
            "baseline_failed": self.baseline_failed,
            "files_changed": self.files_changed,
            "duration_seconds": round(self.duration_seconds, 2),
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "metadata": self.metadata,
        }


@dataclass
class EvalSuiteResult:
    """Aggregate results across all task × CLI combinations."""

    results: list[EvalResult] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0.0

    def by_cli(self) -> dict[str, list[EvalResult]]:
        """Group results by CLI name."""
        grouped: dict[str, list[EvalResult]] = {}
        for r in self.results:
            grouped.setdefault(r.cli_name, []).append(r)
        return grouped

    def by_task(self) -> dict[str, list[EvalResult]]:
        """Group results by task ID."""
        grouped: dict[str, list[EvalResult]] = {}
        for r in self.results:
            grouped.setdefault(r.task_id, []).append(r)
        return grouped

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Eval Suite: {self.passed}/{self.total} passed ({self.pass_rate:.0f}%)",
            f"Duration: {self.duration_seconds:.1f}s",
            "",
        ]

        # Per-CLI breakdown
        for cli_name, results in sorted(self.by_cli().items()):
            cli_passed = sum(1 for r in results if r.passed)
            cli_total = len(results)
            avg_time = sum(r.duration_seconds for r in results) / cli_total if cli_total else 0
            lines.append(
                f"  {cli_name}: {cli_passed}/{cli_total} "
                f"({cli_passed / cli_total * 100:.0f}%) "
                f"avg {avg_time:.1f}s"
            )

        lines.append("")

        # Per-task breakdown
        for task_id, results in sorted(self.by_task().items()):
            statuses = []
            for r in sorted(results, key=lambda x: x.cli_name):
                mark = "✓" if r.passed else "✗"
                statuses.append(f"{r.cli_name}={mark}")
            lines.append(f"  {task_id}: {', '.join(statuses)}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 1),
            "duration_seconds": round(self.duration_seconds, 2),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class EvalRunner:
    """
    Runs eval tasks through CodingAgent CLI adapters.

    Usage:
        runner = EvalRunner(cli_types=["claude-code", "codex"])
        results = await runner.run_all(tasks)
        print(results.summary())
    """

    def __init__(
        self,
        cli_types: list[str] | None = None,
        cli_configs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            cli_types: List of CLI adapter names to test (default: ["claude-code"])
            cli_configs: Per-CLI kwargs, e.g. {"codex": {"sandbox": "workspace-write"}}
        """
        self.cli_types = cli_types or ["claude-code"]
        self.cli_configs = cli_configs or {}

    async def run_all(self, tasks: list[EvalTask]) -> EvalSuiteResult:
        """Run all tasks with all configured CLIs."""
        suite = EvalSuiteResult(
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        start = time.monotonic()

        for task in tasks:
            for cli_type in self.cli_types:
                result = await self.run_one(task, cli_type)
                suite.results.append(result)

        suite.duration_seconds = time.monotonic() - start
        suite.completed_at = datetime.now(timezone.utc).isoformat()
        return suite

    async def run_one(self, task: EvalTask, cli_type: str) -> EvalResult:
        """
        Run a single task with a single CLI.

        Steps:
            1. Copy seed repo to temp dir
            2. Init git if needed
            3. Verify baseline (tests should FAIL)
            4. Invoke CLI agent
            5. Verify result (tests should PASS)
            6. Record result
        """
        result = EvalResult(
            task_id=task.task_id,
            cli_name=cli_type,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        start = time.monotonic()

        temp_dir = tempfile.mkdtemp(prefix=f"eval-{task.task_id}-{cli_type}-")
        work_dir = Path(temp_dir) / "workspace"

        try:
            # Step 1: Copy seed repo
            logger.info("[eval] %s × %s: Copying seed repo", task.task_id, cli_type)
            shutil.copytree(task.seed_repo, work_dir)

            # Step 2: Ensure it's a git repo
            await self._ensure_git_repo(work_dir)

            # Step 3: Verify baseline — tests should FAIL
            logger.info("[eval] %s × %s: Checking baseline (tests should fail)", task.task_id, cli_type)
            baseline = await self._run_verify(work_dir, task.verify_command)
            result.baseline_failed = not baseline.success

            if baseline.success:
                logger.warning(
                    "[eval] %s × %s: Baseline tests PASS — task may be trivial or broken",
                    task.task_id, cli_type,
                )
                result.error = "Baseline tests already pass — invalid eval task"
                result.passed = False
                return result

            # Step 4: Invoke CLI agent
            logger.info("[eval] %s × %s: Invoking %s CLI", task.task_id, cli_type, cli_type)
            cli_kwargs = self.cli_configs.get(cli_type, {})
            cli = create_cli(cli_type, **cli_kwargs)

            request = CLIRequest(
                prompt=self._build_prompt(task),
                work_dir=work_dir,
                focus_files=task.focus_paths,
                context=task.context,
                timeout=task.max_time_seconds,
            )

            cli_response = await cli.run(request)
            result.agent_output = cli_response.output[:5000]  # Cap for storage
            result.exit_code = cli_response.exit_code

            if not cli_response.success:
                logger.warning(
                    "[eval] %s × %s: CLI agent reported failure (exit_code=%d): %s",
                    task.task_id, cli_type, cli_response.exit_code,
                    cli_response.error[:200] or cli_response.output[:200],
                )
                # Don't return early — the agent may have partially succeeded.
                # We still run verification tests to grade the result.

            # Step 5: Check what files changed
            try:
                ws = GitWorkspace(repo_url="", work_dir=work_dir)
                ws._work_dir = work_dir
                ws._cloned = True
                result.files_changed = await ws.get_changed_files()
            except Exception:
                pass  # Non-critical

            # Step 6: Verify result — tests should PASS
            logger.info("[eval] %s × %s: Verifying (tests should pass)", task.task_id, cli_type)
            verify = await self._run_verify(work_dir, task.verify_command)
            result.test_output = verify.stdout[:5000]
            result.passed = verify.success

            if result.passed:
                logger.info("[eval] %s × %s: ✓ PASSED", task.task_id, cli_type)
            else:
                logger.info("[eval] %s × %s: ✗ FAILED", task.task_id, cli_type)
                if not cli_response.success:
                    result.error = (
                        f"CLI failed (exit_code={cli_response.exit_code}): "
                        f"{cli_response.error[:300] or cli_response.output[:300]}"
                    )
                else:
                    result.error = "Tests still failing after agent work"
                logger.debug(
                    "[eval] %s × %s: test output:\n%s",
                    task.task_id, cli_type, verify.stdout[:2000],
                )

        except Exception as e:
            logger.error("[eval] %s × %s: Error: %s", task.task_id, cli_type, e)
            result.error = str(e)
            result.passed = False

        finally:
            result.duration_seconds = time.monotonic() - start
            result.completed_at = datetime.now(timezone.utc).isoformat()
            # Clean up temp dir
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

        return result

    def _build_prompt(self, task: EvalTask) -> str:
        """Build the prompt from task definition."""
        sections = [f"## Objective\n{task.objective}"]

        if task.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria)
            sections.append(f"## Acceptance Criteria\n{criteria}")

        if task.focus_paths:
            sections.append(f"## Scope\nFocus on: {', '.join(task.focus_paths)}")

        if task.protected_paths:
            sections.append(f"## Constraints\nDo NOT modify: {', '.join(task.protected_paths)}")

        sections.append(
            f"## Verification\n"
            f"The task is complete when `{task.verify_command}` passes."
        )

        return "\n\n".join(sections)

    async def _run_verify(self, work_dir: Path, command: str) -> Any:
        """Run the verification command and return result."""
        parts = command.split()
        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=120.0,
            )
        except asyncio.TimeoutError:
            return type("Result", (), {
                "success": False,
                "stdout": "",
                "stderr": "Verification timed out",
            })()

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return type("Result", (), {
            "success": proc.returncode == 0,
            "stdout": stdout + "\n" + stderr,
            "stderr": stderr,
        })()

    async def _ensure_git_repo(self, work_dir: Path) -> None:
        """Initialize git if the directory isn't already a repo."""
        git_dir = work_dir / ".git"
        if git_dir.exists():
            return

        proc = await asyncio.create_subprocess_exec(
            "git", "init",
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Configure git for commits
        for cmd in [
            ["git", "config", "user.email", "eval@agent-orchestrator.test"],
            ["git", "config", "user.name", "Eval Runner"],
            ["git", "add", "."],
            ["git", "commit", "-m", "Initial seed repo state"],
        ]:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()


# ---------------------------------------------------------------------------
# Report saving
# ---------------------------------------------------------------------------


def save_results(suite: EvalSuiteResult, output_dir: Path) -> Path:
    """Save eval results to JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_file = output_dir / f"eval-{timestamp}.json"

    with open(output_file, "w") as f:
        json.dump(suite.to_dict(), f, indent=2)

    logger.info("Results saved to %s", output_file)
    return output_file
