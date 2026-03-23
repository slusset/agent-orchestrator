"""
Coding Agent: Implements features and creates Pull Requests.

Receives a CodingBundle, clones the repo, creates a feature branch,
implements changes (LLM-driven), runs tests, and creates a PR.
The PR URL is the primary artifact returned to the PA.

Workflow:
  1. Clone repo, create feature branch (git_workspace)
  2. Analyze objective + acceptance criteria (LLM)
  3. Implement changes (LLM → file writes)
  4. Run tests (subprocess)
  5. If tests fail → attempt one fix cycle (LLM)
  6. Push branch, create PR (git + gh CLI)
  7. Report PR URL as artifact

Traceability:
  Persona: specs/personas/coding-agent.md
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
  Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.agent_cli import AgentCLI, CLIRequest, CLIResponse, ClaudeCodeCLI, create_cli
from src.agents.base import BaseAgent
from src.agents.git_workspace import GitWorkspace, TestResults, make_branch_name
from src.contracts.coding_bundle import CodingBundle
from src.contracts.status_reporter import StatusReporter

logger = logging.getLogger(__name__)

# Default CLI used when none is injected
DEFAULT_CLI_TYPE = "claude-code"


class CodingAgent(BaseAgent):
    """
    Implements features based on a CodingBundle.

    Workflow:
    1. Clone/checkout repo, create feature branch
    2. Analyze objective and acceptance criteria
    3. Implement changes (LLM-driven)
    4. Run tests
    5. Create PR
    6. Report completion with PR URL as artifact
    """

    agent_type = "coding"

    def __init__(
        self,
        bundle: CodingBundle,
        reporter: StatusReporter | None = None,
        cli: AgentCLI | None = None,
    ) -> None:
        super().__init__(bundle, reporter)
        self.coding_bundle = bundle  # Typed access to CodingBundle-specific fields
        # CLI can be injected directly or resolved from bundle config
        # If bundle carries resolved_env, pass it to the CLI for subprocess injection
        if cli:
            self.cli = cli
        else:
            cli_kwargs = dict(bundle.cli_args)
            if bundle.resolved_env:
                cli_kwargs["env"] = {**cli_kwargs.get("env", {}), **bundle.resolved_env}
            self.cli = create_cli(bundle.cli_type, **cli_kwargs)
        self._workspace: GitWorkspace | None = None

    async def execute(self) -> dict[str, Any]:
        """
        Execute the coding task.

        Sets up a git workspace, implements changes, runs tests,
        and creates a PR. Returns artifacts including the PR URL.
        """
        workspace = GitWorkspace(
            repo_url=self.coding_bundle.repo_url,
            base_branch=self.coding_bundle.base_branch,
        )
        self._workspace = workspace

        try:
            # Step 1: Setup workspace
            await self.reporter.heartbeat("Cloning repository and creating branch", progress_pct=5)
            branch_name = await self._setup_workspace(workspace)

            # Step 2: Analyze the task
            await self.reporter.heartbeat("Analyzing objective and planning implementation", progress_pct=15)
            plan = await self._analyze_task()

            # Step 3: Implement changes
            await self.reporter.heartbeat("Implementing changes", progress_pct=30)
            files_changed = await self._implement(workspace, plan)

            # Step 4: Commit changes
            await self.reporter.heartbeat("Committing changes", progress_pct=60)
            if files_changed:
                await workspace.commit_all(
                    f"Implement: {self.coding_bundle.objective}\n\n"
                    f"Task: {self.coding_bundle.task_id}"
                )

            # Step 5: Run tests
            test_results = TestResults(passed=True, count=0)
            if self.coding_bundle.run_unit_tests or self.coding_bundle.run_integration_tests:
                await self.reporter.heartbeat("Running tests", progress_pct=65)
                test_results = await self._run_tests(workspace)

                if not test_results.passed:
                    # Try to fix failures — one retry cycle
                    await self.reporter.heartbeat(
                        f"Fixing {len(test_results.failures)} test failure(s)",
                        progress_pct=75,
                    )
                    fix_files = await self._fix_test_failures(workspace, test_results)

                    if fix_files:
                        await workspace.commit_all(
                            f"Fix test failures for: {self.coding_bundle.objective}"
                        )

                    await self.reporter.heartbeat("Re-running tests after fix", progress_pct=80)
                    test_results = await self._run_tests(workspace)

                    if not test_results.passed:
                        raise RuntimeError(
                            f"Tests failed after fix attempt: {test_results.failures}"
                        )

            # Step 6: Push and create PR
            await self.reporter.heartbeat("Pushing branch and creating pull request", progress_pct=90)
            pr_url = await self._create_pr(workspace, branch_name, files_changed)

            return {
                "summary": f"Implemented '{self.coding_bundle.objective}' and created PR",
                "artifacts": [pr_url],
                "metadata": {
                    "branch": branch_name,
                    "files_changed": files_changed,
                    "tests_passed": test_results.passed,
                    "test_count": test_results.count,
                },
            }

        finally:
            workspace.cleanup()
            self._workspace = None

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    async def _setup_workspace(self, workspace: GitWorkspace) -> str:
        """Clone repo and create feature branch. Returns branch name."""
        branch_name = make_branch_name(
            prefix=self.coding_bundle.branch_prefix,
            task_id=self.coding_bundle.task_id,
            objective=self.coding_bundle.objective,
        )

        await workspace.clone()
        await workspace.create_branch(branch_name)

        logger.info(
            "[coding] Workspace ready: %s on branch %s",
            workspace.work_dir,
            branch_name,
        )
        return branch_name

    async def _analyze_task(self) -> dict[str, Any]:
        """
        Analyze objective and create implementation plan.

        Returns a plan dict with the prompt and scope for the CLI.
        The actual analysis is done by the CLI agent during _implement.
        """
        logger.info(
            "[coding] Analyzing: %s with %d acceptance criteria",
            self.coding_bundle.objective,
            len(self.coding_bundle.acceptance_criteria),
        )

        return {
            "focus_paths": self.coding_bundle.focus_paths,
            "protected_paths": self.coding_bundle.protected_paths,
        }

    async def _implement(
        self,
        workspace: GitWorkspace,
        plan: dict[str, Any],
    ) -> list[str]:
        """
        Execute the implementation via the pluggable agent CLI.

        Sends the objective, acceptance criteria, and scope constraints
        to the configured CLI agent (Claude Code, Codex, etc.) which
        does the actual code generation in the workspace directory.
        """
        prompt = self._build_implementation_prompt(plan)

        request = CLIRequest(
            prompt=prompt,
            work_dir=workspace.work_dir,
            focus_files=self.coding_bundle.focus_paths,
            read_only_files=[],
            context=self.coding_bundle.context,
            timeout=self.coding_bundle.timeout_minutes * 60,
        )

        logger.info("[coding] Invoking %s CLI for implementation", self.cli.name)
        response = await self.cli.run(request)

        if not response.success:
            raise RuntimeError(
                f"CLI agent ({self.cli.name}) failed: {response.error or response.output[:500]}"
            )

        # Discover what files changed via git
        files_changed = await workspace.get_changed_files()

        logger.info(
            "[coding] CLI agent completed: %d files changed",
            len(files_changed),
        )
        return files_changed

    def _build_implementation_prompt(self, plan: dict[str, Any]) -> str:
        """Build the prompt sent to the CLI agent."""
        sections = [
            f"## Objective\n{self.coding_bundle.objective}",
        ]

        if self.coding_bundle.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in self.coding_bundle.acceptance_criteria)
            sections.append(f"## Acceptance Criteria\n{criteria}")

        if plan.get("focus_paths"):
            paths = ", ".join(plan["focus_paths"])
            sections.append(f"## Scope\nFocus your changes on: {paths}")

        if plan.get("protected_paths"):
            paths = ", ".join(plan["protected_paths"])
            sections.append(f"## Constraints\nDo NOT modify these paths: {paths}")

        if self.coding_bundle.run_unit_tests:
            frameworks = ", ".join(self.coding_bundle.test_frameworks) if self.coding_bundle.test_frameworks else "pytest"
            sections.append(f"## Testing\nWrite or update tests. Test framework: {frameworks}")

        return "\n\n".join(sections)

    async def _run_tests(self, workspace: GitWorkspace) -> TestResults:
        """Run tests based on CodingBundle test configuration."""
        frameworks = self.coding_bundle.test_frameworks or ["pytest"]

        logger.info(
            "[coding] Running tests: unit=%s, integration=%s, frameworks=%s",
            self.coding_bundle.run_unit_tests,
            self.coding_bundle.run_integration_tests,
            frameworks,
        )

        results = await workspace.run_tests(
            frameworks=frameworks,
            focus_paths=self.coding_bundle.focus_paths or None,
        )

        # Check coverage threshold if specified
        if (
            self.coding_bundle.min_coverage_pct is not None
            and results.coverage_pct is not None
            and results.coverage_pct < self.coding_bundle.min_coverage_pct
        ):
            results.passed = False
            results.failures.append(
                f"Coverage {results.coverage_pct:.1f}% below minimum "
                f"{self.coding_bundle.min_coverage_pct}%"
            )

        return results

    async def _fix_test_failures(
        self,
        workspace: GitWorkspace,
        test_results: TestResults,
    ) -> list[str]:
        """
        Attempt to fix test failures using the CLI agent.

        Sends the test output to the CLI agent and asks it to fix
        the failing tests. Returns list of files modified.
        """
        logger.info(
            "[coding] Attempting to fix %d failure(s) via %s CLI",
            len(test_results.failures),
            self.cli.name,
        )

        # Build a fix-specific prompt with test output
        failures_text = "\n".join(test_results.failures) if test_results.failures else "See output below"
        prompt = (
            f"## Fix Test Failures\n\n"
            f"The following tests are failing:\n{failures_text}\n\n"
            f"## Test Output\n```\n{test_results.output[-3000:]}\n```\n\n"
            f"Fix the code so all tests pass. Do not modify the test expectations "
            f"unless they are clearly wrong."
        )

        request = CLIRequest(
            prompt=prompt,
            work_dir=workspace.work_dir,
            focus_files=self.coding_bundle.focus_paths,
            timeout=min(self.coding_bundle.timeout_minutes * 60, 300),  # Cap fix attempts at 5min
        )

        response = await self.cli.run(request)

        if not response.success:
            logger.warning("[coding] Fix attempt failed: %s", response.error[:200])
            return []

        return await workspace.get_changed_files()

    async def _create_pr(
        self,
        workspace: GitWorkspace,
        branch_name: str,
        files_changed: list[str],
    ) -> str:
        """Push branch and create pull request. Returns PR URL."""
        title = self._build_pr_title()

        # Build PR body
        body = self._build_pr_body(files_changed)

        # Push the branch
        await workspace.push()

        # Create the PR
        pr_info = await workspace.create_pr(
            title=title,
            body=body,
            draft=self.coding_bundle.draft_pr,
        )

        logger.info(
            "[coding] Created %sPR #%d: %s",
            "draft " if self.coding_bundle.draft_pr else "",
            pr_info.number,
            pr_info.url,
        )

        return pr_info.url

    def _build_pr_title(self) -> str:
        """Build a concise PR title from the objective.

        Takes the first sentence (or first 65 chars) of the objective
        and prepends the optional pr_title_prefix. Avoids dumping the
        full multi-sentence prompt as the PR title.
        """
        objective = self.coding_bundle.objective

        # Take first sentence
        for delim in (". ", ".\n", "\n"):
            if delim in objective:
                objective = objective[:objective.index(delim)]
                break

        # Truncate to reasonable PR title length
        max_len = 65
        if len(objective) > max_len:
            objective = objective[:max_len].rsplit(" ", 1)[0] + "..."

        prefix = self.coding_bundle.pr_title_prefix
        if prefix:
            return f"{prefix} {objective}".strip()
        return objective.strip()

    def _build_pr_body(self, files_changed: list[str]) -> str:
        """Build the PR body from template or defaults."""
        if self.coding_bundle.pr_template:
            return self.coding_bundle.pr_template

        sections = [
            f"## Objective\n{self.coding_bundle.objective}",
        ]

        if self.coding_bundle.acceptance_criteria:
            criteria = "\n".join(
                f"- [ ] {c}" for c in self.coding_bundle.acceptance_criteria
            )
            sections.append(f"## Acceptance Criteria\n{criteria}")

        if files_changed:
            file_list = "\n".join(f"- `{f}`" for f in files_changed)
            sections.append(f"## Files Changed\n{file_list}")

        sections.append(
            f"---\n"
            f"Task ID: `{self.coding_bundle.task_id}`\n"
            f"Agent: `coding`"
        )

        return "\n\n".join(sections)
