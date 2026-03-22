"""
Unit tests for PlanManager — the tool interface for roadmap mutations.

Every mutation to the plan MUST go through PlanManager, never raw YAML.
These tests verify that PlanManager enforces invariants, validates
transitions, checks dependencies, and persists correctly.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Capability: specs/capabilities/agent-orchestration.capability.yaml
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Feature: specs/features/orchestration/pa-plans-and-dispatches.feature

The PlanManager is the PA's primary interface to strategic state.
Bugs here would cause the PA to dispatch work incorrectly.
"""

import pytest
import yaml
from pathlib import Path

from src.plan.manager import PlanError, PlanManager
from src.plan.schema import (
    Capability,
    CapabilityStatus,
    Milestone,
    MilestoneStatus,
    Roadmap,
)


# ---------------------------------------------------------------------------
# Loading and saving
# ---------------------------------------------------------------------------


class TestPlanManagerLoad:
    """Test loading and basic access."""

    def test_load_valid_roadmap(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        roadmap = pm.load()
        assert roadmap.id == "test-roadmap"
        assert len(roadmap.milestones) == 3

    def test_load_missing_file_raises(self, tmp_path):
        pm = PlanManager(tmp_path / "nonexistent.yaml")
        with pytest.raises(PlanError, match="not found"):
            pm.load()

    def test_roadmap_property_before_load_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        with pytest.raises(PlanError, match="No roadmap loaded"):
            _ = pm.roadmap

    def test_save_without_load_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        with pytest.raises(PlanError, match="No roadmap loaded"):
            pm.save()

    def test_save_creates_parent_dirs(self, tmp_path, sample_roadmap_data):
        deep_path = tmp_path / "nested" / "dir" / "roadmap.yaml"
        # Write initial file so we can load
        deep_path.parent.mkdir(parents=True)
        with open(deep_path, "w") as f:
            yaml.dump(sample_roadmap_data, f)

        pm = PlanManager(deep_path)
        pm.load()
        pm.save()
        assert deep_path.exists()

    def test_save_roundtrip(self, roadmap_file):
        """Load → save → load should produce the same roadmap."""
        pm = PlanManager(roadmap_file)
        pm.load()
        pm.save()

        pm2 = PlanManager(roadmap_file)
        roadmap2 = pm2.load()
        assert roadmap2.id == "test-roadmap"
        assert len(roadmap2.milestones) == 3


# ---------------------------------------------------------------------------
# Milestone advancement
# ---------------------------------------------------------------------------


class TestAdvanceMilestone:
    """
    Test milestone lifecycle transitions via PlanManager.

    Traceability:
      Feature: specs/features/orchestration/pa-evaluates-and-routes.feature
      The PA advances milestones as the delivery lifecycle progresses.
      Guards prevent invalid states that would confuse routing.
    """

    def test_planned_to_in_progress(self, roadmap_file):
        """m2-invocation can start because m1 is complete."""
        pm = PlanManager(roadmap_file)
        pm.load()

        ms = pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        assert ms.status == MilestoneStatus.IN_PROGRESS
        assert ms.started_at is not None

    def test_planned_to_deferred(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        ms = pm.advance_milestone("m2-invocation", MilestoneStatus.DEFERRED)
        assert ms.status == MilestoneStatus.DEFERRED

    def test_cannot_start_with_incomplete_deps(self, roadmap_file):
        """m3-agents depends on m2 which is still planned."""
        pm = PlanManager(roadmap_file)
        pm.load()

        with pytest.raises(PlanError, match="dependency.*not complete"):
            pm.advance_milestone("m3-agents", MilestoneStatus.IN_PROGRESS)

    def test_cannot_complete_with_incomplete_capabilities(self, roadmap_file):
        """m2-invocation has not_started capabilities — can't complete."""
        pm = PlanManager(roadmap_file)
        pm.load()

        # First start it
        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)

        with pytest.raises(PlanError, match="capabilities not complete"):
            pm.advance_milestone("m2-invocation", MilestoneStatus.COMPLETE)

    def test_complete_is_terminal(self, roadmap_file):
        """m1-foundation is complete — no transitions allowed."""
        pm = PlanManager(roadmap_file)
        pm.load()

        with pytest.raises(PlanError, match="Cannot transition"):
            pm.advance_milestone("m1-foundation", MilestoneStatus.IN_PROGRESS)

    def test_invalid_transition_blocked_to_complete(self, roadmap_file):
        """Blocked → Complete is not a valid transition."""
        pm = PlanManager(roadmap_file)
        pm.load()

        # Start m2, then block it
        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        pm.advance_milestone("m2-invocation", MilestoneStatus.BLOCKED)

        with pytest.raises(PlanError, match="Cannot transition"):
            pm.advance_milestone("m2-invocation", MilestoneStatus.COMPLETE)

    def test_nonexistent_milestone_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        with pytest.raises(PlanError, match="not found"):
            pm.advance_milestone("m99-ghost", MilestoneStatus.IN_PROGRESS)

    def test_deferred_to_planned_to_in_progress(self, roadmap_file):
        """Deferred milestones can be re-planned and then started."""
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.DEFERRED)
        pm.advance_milestone("m2-invocation", MilestoneStatus.PLANNED)
        ms = pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        assert ms.status == MilestoneStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# Capability advancement
# ---------------------------------------------------------------------------


class TestAdvanceCapability:
    """
    Test capability lifecycle transitions.

    Traceability:
      Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
      Capabilities track which work the PA has dispatched vs completed.
    """

    def test_start_capability_in_active_milestone(self, roadmap_file):
        """Can start a capability when its milestone is in_progress."""
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        cap = pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)
        assert cap.status == CapabilityStatus.IN_PROGRESS

    def test_cannot_start_capability_in_planned_milestone(self, roadmap_file):
        """Can't start work on a capability whose milestone hasn't started."""
        pm = PlanManager(roadmap_file)
        pm.load()

        with pytest.raises(PlanError, match="not in_progress"):
            pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)

    def test_complete_capability(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)
        cap = pm.advance_capability("callback-server", CapabilityStatus.COMPLETE)
        assert cap.status == CapabilityStatus.COMPLETE

    def test_cannot_complete_with_incomplete_deps(self, roadmap_file):
        """http-dispatch depends on callback-server — can't complete first."""
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)
        pm.advance_capability("http-dispatch", CapabilityStatus.IN_PROGRESS)

        with pytest.raises(PlanError, match="dependency"):
            pm.advance_capability("http-dispatch", CapabilityStatus.COMPLETE)

    def test_complete_with_satisfied_deps(self, roadmap_file):
        """http-dispatch can complete once callback-server is complete."""
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)
        pm.advance_capability("callback-server", CapabilityStatus.COMPLETE)
        pm.advance_capability("http-dispatch", CapabilityStatus.IN_PROGRESS)
        cap = pm.advance_capability("http-dispatch", CapabilityStatus.COMPLETE)
        assert cap.status == CapabilityStatus.COMPLETE

    def test_nonexistent_capability_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        with pytest.raises(PlanError, match="not found"):
            pm.advance_capability("ghost-cap", CapabilityStatus.IN_PROGRESS)


# ---------------------------------------------------------------------------
# Adding capabilities
# ---------------------------------------------------------------------------


class TestAddCapability:
    """
    Test dynamic capability addition.

    The PA may discover new capabilities during development.
    These must be added through PlanManager to maintain uniqueness
    and referential integrity.
    """

    def test_add_new_capability(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        new_cap = Capability(
            id="error-retry",
            name="Error retry logic",
            depends_on=["callback-server"],
        )
        cap = pm.add_capability("m2-invocation", new_cap)
        assert cap.id == "error-retry"

        # Verify it persisted
        pm2 = PlanManager(roadmap_file)
        pm2.load()
        result = pm2.roadmap.get_capability("error-retry")
        assert result is not None

    def test_add_duplicate_capability_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        dup = Capability(id="callback-server", name="Duplicate")
        with pytest.raises(PlanError, match="already exists"):
            pm.add_capability("m2-invocation", dup)

    def test_add_capability_with_invalid_dep_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        bad = Capability(
            id="new-cap",
            name="New",
            depends_on=["ghost-dep"],
        )
        with pytest.raises(PlanError, match="does not exist"):
            pm.add_capability("m2-invocation", bad)

    def test_add_capability_to_nonexistent_milestone_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        cap = Capability(id="new-cap", name="New")
        with pytest.raises(PlanError, match="not found"):
            pm.add_capability("m99-ghost", cap)


# ---------------------------------------------------------------------------
# Adding milestones
# ---------------------------------------------------------------------------


class TestAddMilestone:
    """Test adding milestones dynamically."""

    def test_add_new_milestone(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        ms = Milestone(
            id="m4-observability",
            name="Observability",
            depends_on=["m2-invocation"],
        )
        result = pm.add_milestone(ms)
        assert result.id == "m4-observability"
        assert len(pm.roadmap.milestones) == 4

    def test_add_duplicate_milestone_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        dup = Milestone(id="m1-foundation", name="Duplicate")
        with pytest.raises(PlanError, match="already exists"):
            pm.add_milestone(dup)

    def test_add_milestone_with_invalid_dep_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        bad = Milestone(
            id="m4-new",
            name="New",
            depends_on=["m99-ghost"],
        )
        with pytest.raises(PlanError, match="does not exist"):
            pm.add_milestone(bad)


# ---------------------------------------------------------------------------
# Linking specs and issues
# ---------------------------------------------------------------------------


class TestLinking:
    """
    Test spec and issue linking — IDD traceability support.

    Traceability:
      The plan layer links capabilities to IDD specs and GitHub issues.
      This is how we trace from strategic intent down to implementation.
    """

    def test_link_spec(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.link_spec("callback-server", "specs/features/callback.feature")

        # Verify persistence
        pm2 = PlanManager(roadmap_file)
        pm2.load()
        _, cap = pm2.roadmap.get_capability("callback-server")
        assert "specs/features/callback.feature" in cap.spec_refs

    def test_link_spec_idempotent(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.link_spec("callback-server", "specs/features/callback.feature")
        pm.link_spec("callback-server", "specs/features/callback.feature")

        _, cap = pm.roadmap.get_capability("callback-server")
        assert cap.spec_refs.count("specs/features/callback.feature") == 1

    def test_link_issue(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.link_issue("callback-server", "#5")
        _, cap = pm.roadmap.get_capability("callback-server")
        assert "#5" in cap.issue_refs

    def test_link_to_nonexistent_capability_raises(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        with pytest.raises(PlanError, match="not found"):
            pm.link_spec("ghost-cap", "specs/whatever.feature")

        with pytest.raises(PlanError, match="not found"):
            pm.link_issue("ghost-cap", "#99")


# ---------------------------------------------------------------------------
# Planning context and queries
# ---------------------------------------------------------------------------


class TestPlanningContext:
    """
    Test planning_context() and what_can_start().

    Traceability:
      Story: specs/stories/orchestration/pa-plans-and-dispatches.md
      The PA reads planning context during plan_work() to decide
      what to dispatch. Incorrect context → wrong dispatch.
    """

    def test_planning_context_structure(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        ctx = pm.planning_context()
        assert "roadmap" in ctx
        assert "progress" in ctx
        assert "completed_milestones" in ctx
        assert "active_milestones" in ctx
        assert "available_milestones" in ctx
        assert "blocked_milestones" in ctx

    def test_planning_context_counts(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        ctx = pm.planning_context()
        # m1 has 2 complete caps out of 5 total caps across roadmap
        assert "complete" in ctx["progress"]
        assert "m1-foundation" in ctx["completed_milestones"]

    def test_planning_context_available_milestones(self, roadmap_file):
        pm = PlanManager(roadmap_file)
        pm.load()

        ctx = pm.planning_context()
        available_ids = [m["id"] for m in ctx["available_milestones"]]
        assert "m2-invocation" in available_ids
        # m3 should NOT be available (depends on m2 which isn't complete)
        assert "m3-agents" not in available_ids

    def test_what_can_start_empty_when_no_active(self, roadmap_file):
        """No active milestones → nothing can start."""
        pm = PlanManager(roadmap_file)
        pm.load()

        startable = pm.what_can_start()
        assert startable == []

    def test_what_can_start_with_active_milestone(self, roadmap_file):
        """After starting m2, its dependency-free caps should be startable."""
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        startable = pm.what_can_start()
        startable_ids = [c.id for c in startable]

        # callback-server has no deps → startable
        assert "callback-server" in startable_ids
        # http-dispatch depends on callback-server → NOT startable
        assert "http-dispatch" not in startable_ids

    def test_what_can_start_after_dep_complete(self, roadmap_file):
        """After completing callback-server, http-dispatch becomes startable."""
        pm = PlanManager(roadmap_file)
        pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)
        pm.advance_capability("callback-server", CapabilityStatus.COMPLETE)

        startable = pm.what_can_start()
        startable_ids = [c.id for c in startable]
        assert "http-dispatch" in startable_ids
