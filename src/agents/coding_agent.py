"""
Coding Agent: Implements features and creates Pull Requests.

Receives a CodingBundle, works in isolation on a branch, and signals
completion by creating a PR. The PR URL is the primary artifact.

This is a scaffold — the actual coding logic (LLM calls, file edits,
test runs) will be plugged in. The structure demonstrates:
- How an agent consumes a TaskBundle
- How it reports progress via heartbeats
- How it signals completion with artifacts
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.base import BaseAgent
from src.contracts.coding_bundle import CodingBundle
from src.contracts.status_reporter import StatusReporter

logger = logging.getLogger(__name__)


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

    def __init__(self, bundle: CodingBundle, reporter: StatusReporter | None = None) -> None:
        super().__init__(bundle, reporter)
        self.coding_bundle = bundle  # Typed access to CodingBundle-specific fields

    async def execute(self) -> dict[str, Any]:
        """
        Execute the coding task.

        This scaffold shows the structure. Each step will be filled in
        with real implementations (git operations, LLM calls, etc.).
        """
        # Step 1: Setup workspace
        await self.reporter.heartbeat("Setting up workspace", progress_pct=5)
        branch_name = await self._setup_workspace()

        # Step 2: Analyze the task
        await self.reporter.heartbeat("Analyzing objective and specs", progress_pct=15)
        plan = await self._analyze_task()

        # Step 3: Implement changes
        await self.reporter.heartbeat("Implementing changes", progress_pct=30)
        files_changed = await self._implement(plan)

        # Step 4: Run tests
        await self.reporter.heartbeat("Running tests", progress_pct=70)
        test_results = await self._run_tests()

        if not test_results["passed"]:
            # Try to fix failures
            await self.reporter.heartbeat("Fixing test failures", progress_pct=80)
            files_changed = await self._fix_test_failures(test_results)
            test_results = await self._run_tests()

            if not test_results["passed"]:
                raise RuntimeError(
                    f"Tests failed after fix attempt: {test_results.get('failures', [])}"
                )

        # Step 5: Create PR
        await self.reporter.heartbeat("Creating pull request", progress_pct=90)
        pr_url = await self._create_pr(branch_name, files_changed)

        return {
            "summary": f"Implemented '{self.coding_bundle.objective}' and created PR",
            "artifacts": [pr_url],
            "metadata": {
                "branch": branch_name,
                "files_changed": files_changed,
                "tests_passed": test_results["passed"],
                "test_count": test_results.get("count", 0),
            },
        }

    # ------------------------------------------------------------------
    # Step implementations (scaffolded — plug in real logic here)
    # ------------------------------------------------------------------

    async def _setup_workspace(self) -> str:
        """Clone repo and create feature branch. Returns branch name."""
        task_slug = self.coding_bundle.task_id[:8]
        objective_slug = (
            self.coding_bundle.objective[:30]
            .lower()
            .replace(" ", "-")
            .replace("/", "-")
        )
        branch_name = f"{self.coding_bundle.branch_prefix}{task_slug}-{objective_slug}"

        logger.info(
            "[coding] Would clone %s and create branch %s",
            self.coding_bundle.repo_url,
            branch_name,
        )

        # TODO: Real implementation
        # - git clone <repo_url> or use existing checkout
        # - git checkout -b <branch_name> from <base_branch>
        # - Respect protected_paths and focus_paths

        return branch_name

    async def _analyze_task(self) -> dict[str, Any]:
        """Use LLM to analyze objective and create implementation plan."""
        logger.info(
            "[coding] Analyzing: %s with %d acceptance criteria",
            self.coding_bundle.objective,
            len(self.coding_bundle.acceptance_criteria),
        )

        # TODO: Real implementation
        # - Send objective + context + acceptance_criteria to LLM
        # - Get back a structured plan: files to create/modify, approach, etc.
        # - Consider focus_paths for scope

        return {
            "approach": "scaffold",
            "files_to_modify": [],
            "files_to_create": [],
        }

    async def _implement(self, plan: dict[str, Any]) -> list[str]:
        """Execute the implementation plan. Returns list of changed files."""
        logger.info("[coding] Implementing plan: %s", plan.get("approach"))

        # TODO: Real implementation
        # - For each file in plan, use LLM to generate/modify code
        # - Write files to disk
        # - git add changed files

        return plan.get("files_to_modify", []) + plan.get("files_to_create", [])

    async def _run_tests(self) -> dict[str, Any]:
        """Run tests based on CodingBundle test configuration."""
        logger.info(
            "[coding] Running tests: unit=%s, integration=%s, frameworks=%s",
            self.coding_bundle.run_unit_tests,
            self.coding_bundle.run_integration_tests,
            self.coding_bundle.test_frameworks,
        )

        # TODO: Real implementation
        # - Run pytest / vitest / etc. based on test_frameworks
        # - Parse results
        # - Check coverage against min_coverage_pct

        return {"passed": True, "count": 0, "failures": []}

    async def _fix_test_failures(self, test_results: dict[str, Any]) -> list[str]:
        """Attempt to fix test failures using LLM."""
        logger.info("[coding] Attempting to fix %d failures", len(test_results.get("failures", [])))

        # TODO: Real implementation
        # - Send failure info + code to LLM
        # - Apply fixes
        # - Return list of modified files

        return []

    async def _create_pr(self, branch_name: str, files_changed: list[str]) -> str:
        """Create a pull request. Returns PR URL."""
        title = f"{self.coding_bundle.pr_title_prefix} {self.coding_bundle.objective}".strip()

        logger.info(
            "[coding] Would create %sPR: %s",
            "draft " if self.coding_bundle.draft_pr else "",
            title,
        )

        # TODO: Real implementation
        # - git push origin <branch_name>
        # - gh pr create --title <title> --body <body> [--draft]
        # - Return the PR URL

        # Placeholder — in real implementation, this comes from GitHub API
        return f"https://github.com/example/repo/pull/0"
