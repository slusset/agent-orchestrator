"""
CodingBundle: TaskBundle specialized for the Coding Agent.

Adds repo context, branch strategy, and testing expectations.
The Coding Agent's completion artifact is a Pull Request.
"""

from __future__ import annotations

from pydantic import Field

from .task_bundle import TaskBundle


class RepoContext(TaskBundle):
    """Minimal repo info the Coding Agent needs."""

    repo_url: str
    base_branch: str = "main"
    branch_prefix: str = "feature/"  # Agent creates: feature/{task_id}-description


class TestingExpectations(TaskBundle):
    """What the PA expects in terms of testing."""

    run_unit_tests: bool = True
    run_integration_tests: bool = False
    min_coverage_pct: int | None = None
    test_frameworks: list[str] = Field(default_factory=list)  # pytest, vitest, etc.


class CodingBundle(TaskBundle):
    """
    Hand-off contract for the Coding Agent.

    The agent receives this, does its work in isolation, and signals
    completion by creating a PR. The PR URL becomes the primary artifact
    in the TaskResult.
    """

    # Repo
    repo_url: str
    base_branch: str = "main"
    branch_prefix: str = "feature/"

    # Scope boundaries — what the agent should NOT touch
    protected_paths: list[str] = Field(default_factory=list)  # e.g., [".github/", "infra/"]
    focus_paths: list[str] = Field(default_factory=list)  # e.g., ["src/auth/"]

    # Testing
    run_unit_tests: bool = True
    run_integration_tests: bool = False
    min_coverage_pct: int | None = None
    test_frameworks: list[str] = Field(default_factory=list)

    # PR expectations
    pr_title_prefix: str = ""  # e.g., "[PROJ-123]"
    pr_template: str | None = None  # Markdown template for PR body
    draft_pr: bool = True  # Create as draft, PA/PR Agent promotes when ready
