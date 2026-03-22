"""
Unit tests for AgentCapabilityProfile and default profiles.

Capability profiles are the PA's mental model of its team. They drive
dispatch decisions and skill filtering. Incorrect profiles lead to
wrong agents getting wrong work.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
  Capability: plan/roadmap.yaml → capability-profiles
"""

import pytest

from src.contracts import (
    AgentCapabilityProfile,
    InvocationMethod,
    CODING_AGENT_PROFILE,
    UAT_AGENT_PROFILE,
    DEVOPS_AGENT_PROFILE,
    PR_AGENT_PROFILE,
    DEFAULT_PROFILES,
)


# ---------------------------------------------------------------------------
# AgentCapabilityProfile model
# ---------------------------------------------------------------------------


class TestAgentCapabilityProfile:
    """Test profile construction and query methods."""

    def test_minimal_profile(self):
        profile = AgentCapabilityProfile(
            agent_type="test",
            name="Test Agent",
        )
        assert profile.agent_type == "test"
        assert profile.invocation == InvocationMethod.LOCAL
        assert profile.implicit_skills == []
        assert profile.configurable_skills == []

    def test_has_skill_implicit(self):
        profile = AgentCapabilityProfile(
            agent_type="test",
            name="Test",
            implicit_skills=["git_operations"],
        )
        assert profile.has_skill("git_operations") is True
        assert profile.has_implicit_skill("git_operations") is True
        assert profile.needs_explicit_skill("git_operations") is False

    def test_has_skill_configurable(self):
        profile = AgentCapabilityProfile(
            agent_type="test",
            name="Test",
            configurable_skills=["bdd_specs"],
        )
        assert profile.has_skill("bdd_specs") is True
        assert profile.has_implicit_skill("bdd_specs") is False
        assert profile.needs_explicit_skill("bdd_specs") is True

    def test_has_skill_unknown(self):
        profile = AgentCapabilityProfile(agent_type="test", name="Test")
        assert profile.has_skill("teleportation") is False

    def test_has_tool(self):
        profile = AgentCapabilityProfile(
            agent_type="test",
            name="Test",
            tools=["git", "pytest"],
        )
        assert profile.has_tool("git") is True
        assert profile.has_tool("docker") is False

    def test_supports_language_case_insensitive(self):
        profile = AgentCapabilityProfile(
            agent_type="test",
            name="Test",
            supported_languages=["Python", "TypeScript"],
        )
        assert profile.supports_language("python") is True
        assert profile.supports_language("PYTHON") is True
        assert profile.supports_language("rust") is False

    def test_invocation_methods(self):
        expected = {"local", "http", "queue"}
        actual = {m.value for m in InvocationMethod}
        assert actual == expected


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------


class TestDefaultProfiles:
    """
    Test that default profiles are correctly configured.

    These are the PA's initial knowledge of its team.
    If a profile is wrong, the PA will make bad dispatch decisions.
    """

    def test_all_four_agents_registered(self):
        assert set(DEFAULT_PROFILES.keys()) == {"coding", "uat", "devops", "pr"}

    def test_coding_agent_profile(self):
        p = CODING_AGENT_PROFILE
        assert p.agent_type == "coding"
        assert "git_operations" in p.implicit_skills
        assert "code_generation" in p.implicit_skills
        assert "bdd_specs" in p.configurable_skills
        assert "idd_workflow" in p.configurable_skills
        assert "python" in [l.lower() for l in p.supported_languages]
        assert p.max_concurrent_tasks == 1

    def test_uat_agent_profile(self):
        p = UAT_AGENT_PROFILE
        assert p.agent_type == "uat"
        assert "spec_reading" in p.implicit_skills
        assert "exploratory_testing" in p.configurable_skills
        assert p.max_concurrent_tasks == 2

    def test_devops_agent_profile(self):
        p = DEVOPS_AGENT_PROFILE
        assert p.agent_type == "devops"
        assert "deploy_vercel" in p.implicit_skills
        assert "rollback" in p.configurable_skills

    def test_pr_agent_profile(self):
        p = PR_AGENT_PROFILE
        assert p.agent_type == "pr"
        assert "code_review" in p.implicit_skills
        assert "idd_compliance" in p.configurable_skills
        assert p.max_concurrent_tasks == 3

    def test_coding_agent_knows_git_implicitly(self):
        """PA should NOT include git instructions in CodingBundle."""
        assert CODING_AGENT_PROFILE.has_implicit_skill("git_operations")
        assert not CODING_AGENT_PROFILE.needs_explicit_skill("git_operations")

    def test_coding_agent_needs_idd_explicitly(self):
        """PA MUST include IDD instructions if it wants IDD compliance."""
        assert CODING_AGENT_PROFILE.needs_explicit_skill("idd_workflow")
