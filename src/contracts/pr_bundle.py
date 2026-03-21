"""
PRBundle: TaskBundle specialized for the PR Agent.

Shepherds pull requests through the review process — automated review,
requesting changes, approving, and coordinating with the PA on merge decisions.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .task_bundle import TaskBundle


class PRAction(str, Enum):
    REVIEW = "review"  # Review and provide feedback
    APPROVE = "approve"  # Approve if criteria met
    REQUEST_CHANGES = "request_changes"  # Flag issues
    MERGE = "merge"  # Merge the PR


class PRBundle(TaskBundle):
    """
    Hand-off contract for the PR Agent.

    Reviews PRs against coding standards, acceptance criteria, and
    project conventions. Can approve, request changes, or merge.
    """

    # PR identity
    repo_url: str
    pr_number: int
    pr_url: str

    # What to do
    action: PRAction = PRAction.REVIEW

    # Review criteria
    check_tests_pass: bool = True
    check_coverage: bool = False
    review_guidelines: list[str] = Field(default_factory=list)  # Project-specific rules
    acceptance_criteria: list[str] = Field(default_factory=list)  # From the original story

    # Merge strategy
    merge_method: str = "squash"  # merge, squash, rebase
    delete_branch_after_merge: bool = True
    require_all_checks_pass: bool = True
