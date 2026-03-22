"""
Unit tests for AgentDispatcher — profile-aware dispatch and skill filtering.

The dispatcher bridges the graph's dispatch nodes and actual agent
invocation. It uses capability profiles to determine HOW to invoke
and WHAT skills to include.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
  Domain Model: specs/models/agent/agent.profile.yaml
"""

import pytest

from src.contracts import (
    AgentCapabilityProfile,
    InvocationMethod,
    CODING_AGENT_PROFILE,
    DEFAULT_PROFILES,
)
from src.orchestrator.dispatcher import AgentDispatcher


# ---------------------------------------------------------------------------
# Skill filtering
# ---------------------------------------------------------------------------


class TestSkillFiltering:
    """
    Test get_skills_to_include() — the PA's skill filter.

    Traceability:
      Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
      Scenario: PA builds TaskBundle with only needed skills

    The PA wants to tell an agent to use certain skills, but some
    skills are implicit (the agent already knows). The dispatcher
    filters those out so the bundle only contains explicit instructions.
    """

    def test_filters_implicit_skills(self):
        dispatcher = AgentDispatcher()
        desired = ["git_operations", "bdd_specs", "code_generation"]
        result = dispatcher.get_skills_to_include("coding", desired)

        # git_operations and code_generation are implicit — filtered out
        assert "git_operations" not in result
        assert "code_generation" not in result
        # bdd_specs is configurable — kept
        assert "bdd_specs" in result

    def test_keeps_configurable_skills(self):
        dispatcher = AgentDispatcher()
        desired = ["bdd_specs", "tdd_workflow", "idd_workflow"]
        result = dispatcher.get_skills_to_include("coding", desired)
        assert set(result) == {"bdd_specs", "tdd_workflow", "idd_workflow"}

    def test_unknown_agent_returns_all(self):
        """No profile → can't filter, include everything."""
        dispatcher = AgentDispatcher()
        desired = ["a", "b", "c"]
        result = dispatcher.get_skills_to_include("unknown-agent", desired)
        assert result == desired

    def test_empty_desired_skills(self):
        dispatcher = AgentDispatcher()
        result = dispatcher.get_skills_to_include("coding", [])
        assert result == []

    def test_all_implicit_returns_empty(self):
        """If all desired skills are implicit, nothing to include."""
        dispatcher = AgentDispatcher()
        desired = ["git_operations", "code_generation", "test_writing"]
        result = dispatcher.get_skills_to_include("coding", desired)
        assert result == []


# ---------------------------------------------------------------------------
# can_handle() — requirement matching
# ---------------------------------------------------------------------------


class TestCanHandle:
    """
    Test can_handle() — checking if an agent meets requirements.

    The PA uses this during planning to pick the right agent.
    """

    def test_coding_can_handle_python(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle("coding", {"language": "python"}) is True

    def test_coding_cannot_handle_rust(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle("coding", {"language": "rust"}) is False

    def test_coding_can_handle_skill(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle(
            "coding", {"skills": ["bdd_specs", "git_operations"]}
        ) is True

    def test_coding_cannot_handle_unknown_skill(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle(
            "coding", {"skills": ["quantum_computing"]}
        ) is False

    def test_coding_can_handle_tool(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle("coding", {"tools": ["git", "pytest"]}) is True

    def test_coding_cannot_handle_missing_tool(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle("coding", {"tools": ["terraform"]}) is False

    def test_unknown_agent_cannot_handle(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle("nonexistent", {"language": "python"}) is False

    def test_combined_requirements(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.can_handle("coding", {
            "language": "python",
            "skills": ["bdd_specs"],
            "tools": ["pytest"],
        }) is True


# ---------------------------------------------------------------------------
# Profile registration
# ---------------------------------------------------------------------------


class TestProfileRegistration:

    def test_default_profiles_loaded(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.get_profile("coding") is not None
        assert dispatcher.get_profile("uat") is not None
        assert dispatcher.get_profile("devops") is not None
        assert dispatcher.get_profile("pr") is not None

    def test_register_custom_profile(self):
        dispatcher = AgentDispatcher()
        custom = AgentCapabilityProfile(
            agent_type="custom",
            name="Custom Agent",
            implicit_skills=["magic"],
        )
        dispatcher.register_profile(custom)
        assert dispatcher.get_profile("custom") is not None
        assert dispatcher.get_profile("custom").name == "Custom Agent"

    def test_override_existing_profile(self):
        dispatcher = AgentDispatcher()
        override = AgentCapabilityProfile(
            agent_type="coding",
            name="Super Coding Agent",
            implicit_skills=["everything"],
        )
        dispatcher.register_profile(override)
        assert dispatcher.get_profile("coding").name == "Super Coding Agent"

    def test_custom_profiles_at_init(self):
        custom_profiles = {
            "special": AgentCapabilityProfile(
                agent_type="special",
                name="Special Agent",
            )
        }
        dispatcher = AgentDispatcher(profiles=custom_profiles)
        assert dispatcher.get_profile("special") is not None
        assert dispatcher.get_profile("coding") is None  # Not loaded


# ---------------------------------------------------------------------------
# Monitoring helpers
# ---------------------------------------------------------------------------


class TestMonitoring:

    def test_is_running_false_when_no_tasks(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.is_running("nonexistent") is False

    def test_get_running_tasks_empty(self):
        dispatcher = AgentDispatcher()
        assert dispatcher.get_running_tasks() == []
