"""
Git Workspace: Manages git operations for the Coding Agent.

Provides async wrappers around git CLI commands for:
- Cloning repositories
- Creating and switching branches
- Staging, committing, and pushing changes
- Creating pull requests via gh CLI

All operations are subprocess-based and async, so they don't
block the event loop. Each workspace tracks its own directory
and branch state.

Traceability:
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
  Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GitError(Exception):
    """Raised when a git command fails."""

    def __init__(self, command: str, returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git command failed (rc={returncode}): {command}\n{stderr}")


@dataclass
class GitResult:
    """Result of a git command execution."""

    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


@dataclass
class TestResults:
    """Structured test execution results."""

    __test__ = False  # Prevent pytest from collecting this dataclass

    passed: bool
    count: int
    failures: list[str] = field(default_factory=list)
    output: str = ""
    coverage_pct: float | None = None


@dataclass
class PRInfo:
    """Pull request information after creation."""

    url: str
    number: int
    title: str
    branch: str


class GitWorkspace:
    """
    Manages a git workspace for an agent task.

    Each GitWorkspace represents a cloned repository with a feature
    branch checked out. All git operations are scoped to this workspace.

    Usage:
        workspace = GitWorkspace(repo_url="https://github.com/org/repo.git")
        await workspace.clone(base_branch="main")
        await workspace.create_branch("feature/my-change")
        # ... agent does work ...
        await workspace.commit_all("Implement feature X")
        await workspace.push()
        pr = await workspace.create_pr(title="Add feature X", body="...")
    """

    def __init__(
        self,
        repo_url: str,
        work_dir: str | Path | None = None,
        base_branch: str = "main",
    ) -> None:
        self.repo_url = repo_url
        self.base_branch = base_branch
        self.branch: str | None = None
        self._work_dir = Path(work_dir) if work_dir else None
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self._cloned = False

    @property
    def work_dir(self) -> Path:
        """The working directory for this workspace."""
        if self._work_dir:
            return self._work_dir
        raise RuntimeError("Workspace not initialized — call clone() first")

    async def _run_git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        timeout: float = 120.0,
    ) -> GitResult:
        """
        Run a git command asynchronously.

        Args:
            *args: Git subcommand and arguments (e.g., "clone", "--depth", "1")
            cwd: Working directory (defaults to workspace dir)
            check: Raise GitError on non-zero exit
            timeout: Command timeout in seconds
        """
        cmd = ["git", *args]
        cmd_str = " ".join(cmd)
        work_cwd = cwd or (self._work_dir if self._work_dir else None)

        logger.debug("[git] Running: %s (cwd=%s)", cmd_str, work_cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=work_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Don't let git prompt for credentials
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise GitError(cmd_str, -1, f"Command timed out after {timeout}s")

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        result = GitResult(
            command=cmd_str,
            returncode=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

        if check and not result.success:
            raise GitError(cmd_str, result.returncode, result.stderr)

        return result

    async def _run_command(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        timeout: float = 300.0,
    ) -> GitResult:
        """
        Run an arbitrary command asynchronously (for gh CLI, test runners, etc.).
        """
        cmd_str = " ".join(args)
        work_cwd = cwd or self._work_dir

        logger.debug("[cmd] Running: %s (cwd=%s)", cmd_str, work_cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=work_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise GitError(cmd_str, -1, f"Command timed out after {timeout}s")

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        result = GitResult(
            command=cmd_str,
            returncode=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

        if check and not result.success:
            raise GitError(cmd_str, result.returncode, stderr)

        return result

    # ------------------------------------------------------------------
    # Repository setup
    # ------------------------------------------------------------------

    async def clone(self, depth: int | None = None) -> Path:
        """
        Clone the repository into the workspace directory.

        Args:
            depth: Shallow clone depth (None for full clone)

        Returns:
            Path to the cloned repository
        """
        if self._work_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="agent-workspace-")
            self._work_dir = Path(self._temp_dir.name)

        # Ensure the parent directory exists
        self._work_dir.mkdir(parents=True, exist_ok=True)

        clone_args = ["clone"]
        if depth:
            clone_args.extend(["--depth", str(depth)])
        clone_args.extend(["--branch", self.base_branch])
        clone_args.append(self.repo_url)

        # Clone into a subdirectory named after the repo
        repo_name = self.repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        clone_target = self._work_dir / repo_name
        clone_args.append(str(clone_target))

        await self._run_git(*clone_args, cwd=self._work_dir, timeout=300.0)
        self._work_dir = clone_target
        self._cloned = True
        self.branch = self.base_branch

        logger.info(
            "[git] Cloned %s (branch: %s) → %s",
            self.repo_url,
            self.base_branch,
            self._work_dir,
        )
        return self._work_dir

    async def create_branch(self, branch_name: str) -> str:
        """
        Create and check out a new branch from the current HEAD.

        Returns the branch name.
        """
        await self._run_git("checkout", "-b", branch_name)
        self.branch = branch_name
        logger.info("[git] Created and checked out branch: %s", branch_name)
        return branch_name

    async def checkout(self, branch_name: str) -> None:
        """Check out an existing branch."""
        await self._run_git("checkout", branch_name)
        self.branch = branch_name

    # ------------------------------------------------------------------
    # Working with files
    # ------------------------------------------------------------------

    async def status(self) -> str:
        """Get git status output."""
        result = await self._run_git("status", "--short")
        return result.stdout

    async def diff(self, staged: bool = False) -> str:
        """Get diff of changes."""
        args = ["diff"]
        if staged:
            args.append("--cached")
        result = await self._run_git(*args)
        return result.stdout

    async def add(self, *paths: str) -> None:
        """Stage files for commit."""
        if not paths:
            paths = (".",)
        await self._run_git("add", *paths)

    async def commit(self, message: str) -> GitResult:
        """Commit staged changes."""
        return await self._run_git("commit", "-m", message)

    async def commit_all(self, message: str) -> GitResult:
        """Stage all changes and commit."""
        await self.add(".")
        return await self.commit(message)

    # ------------------------------------------------------------------
    # Remote operations
    # ------------------------------------------------------------------

    async def push(self, set_upstream: bool = True) -> GitResult:
        """Push current branch to origin."""
        args = ["push"]
        if set_upstream and self.branch:
            args.extend(["--set-upstream", "origin", self.branch])
        return await self._run_git(*args, timeout=120.0)

    async def fetch(self, branch: str | None = None) -> None:
        """Fetch from origin."""
        args = ["fetch", "origin"]
        if branch:
            args.append(branch)
        await self._run_git(*args)

    # ------------------------------------------------------------------
    # Pull request
    # ------------------------------------------------------------------

    async def create_pr(
        self,
        title: str,
        body: str,
        base: str | None = None,
        draft: bool = True,
    ) -> PRInfo:
        """
        Create a pull request using gh CLI.

        Returns PRInfo with URL and number.
        """
        args = [
            "gh", "pr", "create",
            "--title", title,
            "--body", body,
            "--base", base or self.base_branch,
        ]
        if draft:
            args.append("--draft")

        result = await self._run_command(*args, timeout=30.0)

        # gh pr create outputs the PR URL
        pr_url = result.stdout.strip()

        # Extract PR number from URL (e.g., .../pull/42)
        pr_number = 0
        match = re.search(r"/pull/(\d+)", pr_url)
        if match:
            pr_number = int(match.group(1))

        pr_info = PRInfo(
            url=pr_url,
            number=pr_number,
            title=title,
            branch=self.branch or "",
        )

        logger.info("[git] Created PR #%d: %s → %s", pr_number, pr_url, title)
        return pr_info

    # ------------------------------------------------------------------
    # Test execution
    # ------------------------------------------------------------------

    async def run_tests(
        self,
        frameworks: list[str] | None = None,
        focus_paths: list[str] | None = None,
    ) -> TestResults:
        """
        Run tests using the specified framework(s).

        Supports: pytest, vitest, npm test
        Returns structured TestResults.
        """
        frameworks = frameworks or ["pytest"]
        all_passed = True
        total_count = 0
        all_failures: list[str] = []
        all_output: list[str] = []

        for framework in frameworks:
            if framework == "pytest":
                result = await self._run_pytest(focus_paths)
            elif framework in ("vitest", "jest"):
                result = await self._run_npm_test(framework, focus_paths)
            else:
                logger.warning("[test] Unknown test framework: %s, skipping", framework)
                continue

            if not result.passed:
                all_passed = False
            total_count += result.count
            all_failures.extend(result.failures)
            if result.output:
                all_output.append(result.output)

        return TestResults(
            passed=all_passed,
            count=total_count,
            failures=all_failures,
            output="\n".join(all_output),
        )

    async def _run_pytest(self, focus_paths: list[str] | None = None) -> TestResults:
        """Run pytest and parse results."""
        args = ["python", "-m", "pytest", "-v", "--tb=short"]
        if focus_paths:
            args.extend(focus_paths)

        result = await self._run_command(*args, check=False, timeout=300.0)
        output = result.stdout + "\n" + result.stderr

        # Parse pytest output
        passed = result.returncode == 0
        count = 0
        failures: list[str] = []

        # Look for summary line like "5 passed, 2 failed in 1.23s"
        summary_match = re.search(
            r"(\d+) passed(?:.*?(\d+) failed)?", output
        )
        if summary_match:
            passed_count = int(summary_match.group(1))
            failed_count = int(summary_match.group(2) or 0)
            count = passed_count + failed_count

        # Extract failure names
        for line in output.split("\n"):
            if line.startswith("FAILED "):
                failures.append(line.replace("FAILED ", "").strip())

        return TestResults(
            passed=passed,
            count=count,
            failures=failures,
            output=output,
        )

    async def _run_npm_test(
        self, runner: str = "vitest", focus_paths: list[str] | None = None
    ) -> TestResults:
        """Run npm-based test runner (vitest, jest)."""
        args = ["npx", runner, "run"]
        if focus_paths:
            args.extend(focus_paths)

        result = await self._run_command(*args, check=False, timeout=300.0)
        output = result.stdout + "\n" + result.stderr

        return TestResults(
            passed=result.returncode == 0,
            count=0,  # Would need framework-specific parsing
            failures=[output] if result.returncode != 0 else [],
            output=output,
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_changed_files(self) -> list[str]:
        """Get list of files changed since base branch."""
        result = await self._run_git(
            "diff", "--name-only", f"origin/{self.base_branch}...HEAD",
            check=False,
        )
        if result.success and result.stdout:
            return result.stdout.split("\n")
        # Fallback: diff against staging
        result = await self._run_git("diff", "--name-only", "--cached", check=False)
        if result.stdout:
            return result.stdout.split("\n")
        return []

    async def get_file_content(self, path: str) -> str:
        """Read a file from the workspace."""
        full_path = self._work_dir / path
        return full_path.read_text()

    async def write_file(self, path: str, content: str) -> Path:
        """Write content to a file in the workspace."""
        full_path = self._work_dir / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        return full_path

    async def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files matching a glob pattern."""
        return [
            str(p.relative_to(self._work_dir))
            for p in self._work_dir.glob(pattern)
            if p.is_file() and ".git" not in p.parts
        ]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Remove the workspace directory if it's a temp dir."""
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                logger.warning("Failed to clean up workspace: %s", self._work_dir)
            self._temp_dir = None

    async def __aenter__(self) -> GitWorkspace:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.cleanup()


def make_branch_name(
    prefix: str,
    task_id: str,
    objective: str,
    max_length: int = 60,
) -> str:
    """
    Generate a valid git branch name from task metadata.

    Example: feature/a1b2c3d4-add-user-authentication
    """
    task_slug = task_id[:8]
    # Slugify the objective
    objective_slug = re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")
    # Truncate to max length accounting for prefix and task slug
    available = max_length - len(prefix) - len(task_slug) - 1  # -1 for separator
    if available > 0:
        objective_slug = objective_slug[:available].rstrip("-")
    else:
        objective_slug = ""

    if objective_slug:
        return f"{prefix}{task_slug}-{objective_slug}"
    return f"{prefix}{task_slug}"
