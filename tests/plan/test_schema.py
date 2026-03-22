"""
Unit tests for the plan schema layer.

Validates that the roadmap schema enforces structural integrity,
lifecycle transitions, dependency constraints, and circular dependency
detection — the invariants that protect the PA's strategic plan from
LLM-induced drift.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Capability: specs/capabilities/agent-orchestration.capability.yaml
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Feature: specs/features/orchestration/pa-plans-and-dispatches.feature
  Domain Model: specs/models/task/task.lifecycle.yaml

The plan layer sits above IDD specs and below vision. These tests
ensure the schema remains a reliable contract as it evolves.
"""

import pytest
from pydantic import ValidationError

from src.plan.schema import (
    Capability,
    CapabilityStatus,
    Milestone,
    MilestoneStatus,
    MILESTONE_TRANSITIONS,
    Roadmap,
    validate_roadmap,
    _detect_circular_deps,
)


# ---------------------------------------------------------------------------
# Capability model tests
# ---------------------------------------------------------------------------


class TestCapability:
    """Test Capability model instantiation and defaults."""

    def test_minimal_capability(self):
        """A capability needs only id and name."""
        cap = Capability(id="my-cap", name="My Capability")
        assert cap.id == "my-cap"
        assert cap.name == "My Capability"
        assert cap.status == CapabilityStatus.NOT_STARTED
        assert cap.spec_refs == []
        assert cap.issue_refs == []
        assert cap.depends_on == []

    def test_capability_with_all_fields(self):
        cap = Capability(
            id="auth-flow",
            name="Authentication Flow",
            description="OAuth2 + PKCE for SPA",
            status=CapabilityStatus.IN_PROGRESS,
            spec_refs=["specs/features/auth.feature"],
            issue_refs=["#10"],
            depends_on=["user-model"],
        )
        assert cap.status == CapabilityStatus.IN_PROGRESS
        assert "#10" in cap.issue_refs
        assert "user-model" in cap.depends_on

    def test_capability_status_values(self):
        """All expected statuses exist."""
        expected = {"not_started", "in_progress", "complete", "deferred"}
        actual = {s.value for s in CapabilityStatus}
        assert actual == expected


# ---------------------------------------------------------------------------
# Milestone model tests
# ---------------------------------------------------------------------------


class TestMilestone:
    """Test Milestone model and helper methods."""

    def test_minimal_milestone(self):
        ms = Milestone(id="m1-test", name="Test Milestone")
        assert ms.status == MilestoneStatus.PLANNED
        assert ms.capabilities == []
        assert ms.depends_on == []
        assert ms.success_criteria == []

    def test_capability_ids(self):
        ms = Milestone(
            id="m1",
            name="M1",
            capabilities=[
                Capability(id="cap-a", name="A"),
                Capability(id="cap-b", name="B"),
            ],
        )
        assert ms.capability_ids() == ["cap-a", "cap-b"]

    def test_get_capability_found(self):
        cap = Capability(id="cap-a", name="A")
        ms = Milestone(id="m1", name="M1", capabilities=[cap])
        assert ms.get_capability("cap-a") is cap

    def test_get_capability_not_found(self):
        ms = Milestone(id="m1", name="M1")
        assert ms.get_capability("nonexistent") is None

    def test_all_capabilities_complete_true(self):
        ms = Milestone(
            id="m1",
            name="M1",
            capabilities=[
                Capability(id="a", name="A", status=CapabilityStatus.COMPLETE),
                Capability(id="b", name="B", status=CapabilityStatus.COMPLETE),
            ],
        )
        assert ms.all_capabilities_complete() is True

    def test_all_capabilities_complete_false(self):
        ms = Milestone(
            id="m1",
            name="M1",
            capabilities=[
                Capability(id="a", name="A", status=CapabilityStatus.COMPLETE),
                Capability(id="b", name="B", status=CapabilityStatus.IN_PROGRESS),
            ],
        )
        assert ms.all_capabilities_complete() is False

    def test_all_capabilities_complete_empty(self):
        """Milestone with no capabilities is trivially complete."""
        ms = Milestone(id="m1", name="M1")
        assert ms.all_capabilities_complete() is True

    def test_milestone_status_values(self):
        expected = {"planned", "in_progress", "blocked", "complete", "deferred"}
        actual = {s.value for s in MilestoneStatus}
        assert actual == expected


# ---------------------------------------------------------------------------
# Milestone transition table tests
# ---------------------------------------------------------------------------


class TestMilestoneTransitions:
    """
    Verify the transition table that governs milestone lifecycle.

    Traceability:
      Feature: specs/features/orchestration/pa-evaluates-and-routes.feature
      The transition table ensures the PA can only advance milestones
      through valid states.
    """

    def test_planned_can_start_or_defer(self):
        allowed = MILESTONE_TRANSITIONS[MilestoneStatus.PLANNED]
        assert MilestoneStatus.IN_PROGRESS in allowed
        assert MilestoneStatus.DEFERRED in allowed
        assert len(allowed) == 2

    def test_in_progress_can_complete_block_defer(self):
        allowed = MILESTONE_TRANSITIONS[MilestoneStatus.IN_PROGRESS]
        assert MilestoneStatus.COMPLETE in allowed
        assert MilestoneStatus.BLOCKED in allowed
        assert MilestoneStatus.DEFERRED in allowed
        assert len(allowed) == 3

    def test_blocked_can_resume_or_defer(self):
        allowed = MILESTONE_TRANSITIONS[MilestoneStatus.BLOCKED]
        assert MilestoneStatus.IN_PROGRESS in allowed
        assert MilestoneStatus.DEFERRED in allowed
        assert len(allowed) == 2

    def test_complete_is_terminal(self):
        """Complete is a terminal state — no transitions allowed."""
        allowed = MILESTONE_TRANSITIONS[MilestoneStatus.COMPLETE]
        assert allowed == set()

    def test_deferred_can_replan(self):
        allowed = MILESTONE_TRANSITIONS[MilestoneStatus.DEFERRED]
        assert MilestoneStatus.PLANNED in allowed
        assert len(allowed) == 1


# ---------------------------------------------------------------------------
# Roadmap model tests
# ---------------------------------------------------------------------------


class TestRoadmap:
    """
    Test Roadmap model with integrated validation.

    Traceability:
      Journey: specs/journeys/feature-request-lifecycle.md
      The roadmap is the PA's strategic context — these tests ensure
      it validates correctly on construction and provides accurate
      query results.
    """

    def test_valid_roadmap_from_fixture(self, sample_roadmap_data):
        """The fixture data should produce a valid Roadmap."""
        roadmap = Roadmap.model_validate(sample_roadmap_data)
        assert roadmap.id == "test-roadmap"
        assert len(roadmap.milestones) == 3

    def test_roadmap_milestone_ids(self, sample_roadmap):
        assert sample_roadmap.milestone_ids() == [
            "m1-foundation",
            "m2-invocation",
            "m3-agents",
        ]

    def test_get_milestone(self, sample_roadmap):
        m = sample_roadmap.get_milestone("m2-invocation")
        assert m is not None
        assert m.name == "Invocation"

    def test_get_milestone_not_found(self, sample_roadmap):
        assert sample_roadmap.get_milestone("m99-nonexistent") is None

    def test_active_milestones(self, sample_roadmap):
        """In the fixture, no milestones are in_progress."""
        active = sample_roadmap.active_milestones()
        assert len(active) == 0

    def test_available_milestones(self, sample_roadmap):
        """m2-invocation should be available (m1 is complete)."""
        available = sample_roadmap.available_milestones()
        assert len(available) == 1
        assert available[0].id == "m2-invocation"

    def test_get_capability_across_milestones(self, sample_roadmap):
        result = sample_roadmap.get_capability("callback-server")
        assert result is not None
        milestone, cap = result
        assert milestone.id == "m2-invocation"
        assert cap.id == "callback-server"

    def test_get_capability_not_found(self, sample_roadmap):
        assert sample_roadmap.get_capability("nonexistent") is None

    def test_all_capabilities(self, sample_roadmap):
        all_caps = sample_roadmap.all_capabilities()
        cap_ids = [c.id for _, c in all_caps]
        assert "contracts" in cap_ids
        assert "callback-server" in cap_ids
        assert "coding-agent" in cap_ids


# ---------------------------------------------------------------------------
# Validation function tests
# ---------------------------------------------------------------------------


class TestValidateRoadmap:
    """
    Test validate_roadmap() directly for error detection.

    These tests exercise the structural checks that protect
    the plan from invalid states introduced by direct YAML editing
    (which the skill expressly forbids but must be guarded against).
    """

    def test_valid_roadmap_no_errors(self, sample_roadmap):
        """Fixture roadmap should validate clean."""
        errors = validate_roadmap(sample_roadmap)
        assert errors == []

    def test_missing_milestone_dependency(self):
        """Milestone depending on nonexistent milestone — caught by model_validator."""
        with pytest.raises(ValueError, match="does not exist"):
            Roadmap.model_validate(
                {
                    "id": "bad",
                    "name": "Bad",
                    "milestones": [
                        {
                            "id": "m1",
                            "name": "M1",
                            "depends_on": ["m0-doesnt-exist"],
                            "capabilities": [],
                        }
                    ],
                }
            )

    def test_invalid_milestone_dep_raises(self):
        """Constructing a roadmap with invalid milestone deps raises."""
        with pytest.raises(ValueError, match="does not exist"):
            Roadmap.model_validate(
                {
                    "id": "bad",
                    "name": "Bad",
                    "milestones": [
                        {
                            "id": "m1",
                            "name": "M1",
                            "depends_on": ["m0-ghost"],
                            "capabilities": [],
                        }
                    ],
                }
            )

    def test_duplicate_capability_ids_raises(self):
        """Same capability ID in two milestones should fail."""
        with pytest.raises(ValueError, match="appears in both"):
            Roadmap.model_validate(
                {
                    "id": "bad",
                    "name": "Bad",
                    "milestones": [
                        {
                            "id": "m1",
                            "name": "M1",
                            "capabilities": [
                                {"id": "dup-cap", "name": "Dup"},
                            ],
                        },
                        {
                            "id": "m2",
                            "name": "M2",
                            "capabilities": [
                                {"id": "dup-cap", "name": "Dup Again"},
                            ],
                        },
                    ],
                }
            )

    def test_invalid_capability_dep_raises(self):
        """Capability depending on nonexistent capability."""
        with pytest.raises(ValueError, match="does not exist"):
            Roadmap.model_validate(
                {
                    "id": "bad",
                    "name": "Bad",
                    "milestones": [
                        {
                            "id": "m1",
                            "name": "M1",
                            "capabilities": [
                                {
                                    "id": "cap-a",
                                    "name": "A",
                                    "depends_on": ["cap-ghost"],
                                },
                            ],
                        }
                    ],
                }
            )

    def test_in_progress_with_incomplete_dep_raises(self):
        """A milestone that is in_progress but has a non-complete dependency."""
        with pytest.raises(ValueError, match="in_progress but"):
            Roadmap.model_validate(
                {
                    "id": "bad",
                    "name": "Bad",
                    "milestones": [
                        {
                            "id": "m1",
                            "name": "M1",
                            "status": "planned",
                            "capabilities": [],
                        },
                        {
                            "id": "m2",
                            "name": "M2",
                            "status": "in_progress",
                            "depends_on": ["m1"],
                            "capabilities": [],
                        },
                    ],
                }
            )


# ---------------------------------------------------------------------------
# Circular dependency detection tests
# ---------------------------------------------------------------------------


class TestCircularDeps:
    """
    Test _detect_circular_deps() for graph cycle detection.

    This is a critical invariant — circular milestone dependencies
    would cause the PA to deadlock during planning.
    """

    def test_no_cycles(self):
        deps = {
            "m1": [],
            "m2": ["m1"],
            "m3": ["m2"],
        }
        assert _detect_circular_deps(deps) is None

    def test_simple_cycle(self):
        deps = {
            "m1": ["m2"],
            "m2": ["m1"],
        }
        result = _detect_circular_deps(deps)
        assert result is not None
        # The result should contain the cycle
        assert len(result) >= 2

    def test_three_node_cycle(self):
        deps = {
            "m1": ["m3"],
            "m2": ["m1"],
            "m3": ["m2"],
        }
        result = _detect_circular_deps(deps)
        assert result is not None

    def test_self_cycle(self):
        deps = {"m1": ["m1"]}
        result = _detect_circular_deps(deps)
        assert result is not None

    def test_empty_graph(self):
        assert _detect_circular_deps({}) is None

    def test_disconnected_no_cycle(self):
        deps = {
            "m1": [],
            "m2": [],
            "m3": ["m1"],
        }
        assert _detect_circular_deps(deps) is None

    def test_diamond_no_cycle(self):
        """Diamond dependency (m3→m1, m3→m2, m1→m0, m2→m0) is valid."""
        deps = {
            "m0": [],
            "m1": ["m0"],
            "m2": ["m0"],
            "m3": ["m1", "m2"],
        }
        assert _detect_circular_deps(deps) is None

    def test_circular_milestone_deps_raises_on_construction(self):
        """Roadmap with circular milestone deps should fail validation."""
        with pytest.raises(ValueError, match="Circular"):
            Roadmap.model_validate(
                {
                    "id": "bad",
                    "name": "Bad",
                    "milestones": [
                        {
                            "id": "m1",
                            "name": "M1",
                            "depends_on": ["m2"],
                            "capabilities": [],
                        },
                        {
                            "id": "m2",
                            "name": "M2",
                            "depends_on": ["m1"],
                            "capabilities": [],
                        },
                    ],
                }
            )
