"""
Unit tests for TaskBundle and related contracts.

The TaskBundle is THE hand-off contract — the fundamental unit of
communication between the PA and all agents. If it breaks, the entire
orchestrator breaks.

Traceability:
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
  Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
  Domain Model: specs/models/task/task.model.yaml
"""

import pytest
from datetime import datetime

from src.contracts import (
    CodingBundle,
    DevOpsBundle,
    DeployAction,
    DeployEnvironment,
    PRAction,
    PRBundle,
    StatusUpdate,
    TaskBundle,
    TaskPriority,
    TaskResult,
    TaskStatus,
    UATBundle,
    UATProfile,
)


# ---------------------------------------------------------------------------
# TaskBundle (base contract)
# ---------------------------------------------------------------------------


class TestTaskBundle:
    """
    Test the base TaskBundle contract.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
      Scenario: Agent receives a well-formed TaskBundle
    """

    def test_minimal_bundle(self):
        bundle = TaskBundle(
            objective="Implement feature X",
            callback_url="http://localhost:8000/callback",
        )
        assert bundle.objective == "Implement feature X"
        assert bundle.callback_url == "http://localhost:8000/callback"
        assert len(bundle.task_id) == 12  # uuid hex[:12]
        assert bundle.priority == TaskPriority.MEDIUM
        assert bundle.timeout_minutes == 60
        assert bundle.skills == []
        assert bundle.acceptance_criteria == []

    def test_task_id_auto_generated(self):
        """Each bundle gets a unique task_id."""
        b1 = TaskBundle(objective="A", callback_url="http://x")
        b2 = TaskBundle(objective="B", callback_url="http://x")
        assert b1.task_id != b2.task_id

    def test_custom_task_id(self):
        bundle = TaskBundle(
            task_id="custom-123",
            objective="Test",
            callback_url="http://x",
        )
        assert bundle.task_id == "custom-123"

    def test_all_priorities(self):
        for priority in TaskPriority:
            bundle = TaskBundle(
                objective="Test",
                callback_url="http://x",
                priority=priority,
            )
            assert bundle.priority == priority

    def test_bundle_with_skills(self):
        bundle = TaskBundle(
            objective="Test",
            callback_url="http://x",
            skills=["bdd_specs", "tdd_workflow"],
        )
        assert "bdd_specs" in bundle.skills

    def test_bundle_serialization(self):
        """Bundles must serialize cleanly for HTTP transport."""
        bundle = TaskBundle(
            objective="Test",
            callback_url="http://x",
            acceptance_criteria=["AC1", "AC2"],
        )
        data = bundle.model_dump(mode="json")
        assert isinstance(data, dict)
        assert data["objective"] == "Test"
        assert len(data["acceptance_criteria"]) == 2

    def test_bundle_deserialization(self):
        """Bundles must deserialize from JSON (agent receives over HTTP)."""
        data = {
            "task_id": "abc123",
            "objective": "Build widget",
            "callback_url": "http://pa:8000/callback",
            "priority": "high",
            "timeout_minutes": 30,
        }
        bundle = TaskBundle.model_validate(data)
        assert bundle.task_id == "abc123"
        assert bundle.priority == TaskPriority.HIGH
        assert bundle.timeout_minutes == 30


# ---------------------------------------------------------------------------
# CodingBundle
# ---------------------------------------------------------------------------


class TestCodingBundle:
    """
    Test CodingBundle — the contract for the Coding Agent.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
      Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
    """

    def test_coding_bundle_defaults(self):
        bundle = CodingBundle(
            objective="Implement auth flow",
            callback_url="http://x",
            repo_url="https://github.com/org/repo",
        )
        assert bundle.base_branch == "main"
        assert bundle.branch_prefix == "feature/"
        assert bundle.run_unit_tests is True
        assert bundle.draft_pr is True
        assert bundle.protected_paths == []
        assert bundle.focus_paths == []

    def test_coding_bundle_inherits_task_bundle(self):
        bundle = CodingBundle(
            objective="Test",
            callback_url="http://x",
            repo_url="https://github.com/org/repo",
        )
        # Should have all TaskBundle fields
        assert hasattr(bundle, "task_id")
        assert hasattr(bundle, "priority")
        assert hasattr(bundle, "skills")

    def test_coding_bundle_with_paths(self):
        bundle = CodingBundle(
            objective="Test",
            callback_url="http://x",
            repo_url="https://github.com/org/repo",
            protected_paths=["plan/", "specs/"],
            focus_paths=["src/agents/"],
        )
        assert "plan/" in bundle.protected_paths
        assert "src/agents/" in bundle.focus_paths


# ---------------------------------------------------------------------------
# PRBundle
# ---------------------------------------------------------------------------


class TestPRBundle:
    def test_pr_bundle_review(self):
        bundle = PRBundle(
            objective="Review PR #42",
            callback_url="http://x",
            repo_url="https://github.com/org/repo",
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            action=PRAction.REVIEW,
        )
        assert bundle.pr_number == 42
        assert bundle.action == PRAction.REVIEW

    def test_pr_actions(self):
        expected = {"review", "approve", "request_changes", "merge"}
        actual = {a.value for a in PRAction}
        assert actual == expected


# ---------------------------------------------------------------------------
# UATBundle
# ---------------------------------------------------------------------------


class TestUATBundle:
    def test_uat_bundle(self):
        bundle = UATBundle(
            objective="Validate auth flow",
            callback_url="http://x",
            repo_url="https://github.com/org/repo",
            branch="main",
            acceptance_criteria=["User can login with OAuth2"],
        )
        assert bundle.branch == "main"
        assert len(bundle.acceptance_criteria) == 1

    def test_uat_profiles(self):
        expected = {"scripted", "exploratory", "regression", "accessibility"}
        actual = {p.value for p in UATProfile}
        assert actual == expected


# ---------------------------------------------------------------------------
# DevOpsBundle
# ---------------------------------------------------------------------------


class TestDevOpsBundle:
    def test_devops_bundle(self):
        bundle = DevOpsBundle(
            objective="Deploy to staging",
            callback_url="http://x",
            repo_url="https://github.com/org/repo",
            action=DeployAction.DEPLOY,
            target_environment=DeployEnvironment.STAGING,
        )
        assert bundle.action == DeployAction.DEPLOY
        assert bundle.target_environment == DeployEnvironment.STAGING

    def test_deploy_environments(self):
        expected = {"dev", "uat", "staging", "production"}
        actual = {e.value for e in DeployEnvironment}
        assert actual == expected


# ---------------------------------------------------------------------------
# StatusUpdate and TaskResult
# ---------------------------------------------------------------------------


class TestStatusUpdate:
    """
    Test StatusUpdate — what agents POST back to the PA.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
      Journey: specs/journeys/agent-execution-lifecycle.md
    """

    def test_minimal_status_update(self):
        update = StatusUpdate(
            task_id="abc123",
            status=TaskStatus.IN_PROGRESS,
        )
        assert update.task_id == "abc123"
        assert update.progress_pct is None
        assert isinstance(update.timestamp, datetime)

    def test_status_update_with_progress(self):
        update = StatusUpdate(
            task_id="abc123",
            status=TaskStatus.IN_PROGRESS,
            message="Running tests",
            progress_pct=75,
        )
        assert update.progress_pct == 75

    def test_progress_pct_bounds(self):
        """Progress must be 0-100."""
        with pytest.raises(Exception):
            StatusUpdate(
                task_id="x",
                status=TaskStatus.IN_PROGRESS,
                progress_pct=101,
            )
        with pytest.raises(Exception):
            StatusUpdate(
                task_id="x",
                status=TaskStatus.IN_PROGRESS,
                progress_pct=-1,
            )

    def test_all_task_statuses(self):
        expected = {
            "pending", "dispatched", "in_progress",
            "blocked", "completed", "failed", "cancelled",
        }
        actual = {s.value for s in TaskStatus}
        assert actual == expected


class TestTaskResult:
    """
    Test TaskResult — the completion payload agents send.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-reports-progress.feature
    """

    def test_successful_result(self):
        result = TaskResult(
            task_id="abc123",
            success=True,
            summary="Implemented auth flow, created PR",
            artifacts=["https://github.com/org/repo/pull/42"],
        )
        assert result.success is True
        assert len(result.artifacts) == 1
        assert result.errors == []

    def test_failed_result(self):
        result = TaskResult(
            task_id="abc123",
            success=False,
            summary="Tests failed",
            errors=["test_auth.py::test_login FAILED", "3 assertions failed"],
        )
        assert result.success is False
        assert len(result.errors) == 2
