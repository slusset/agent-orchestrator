"""
PlanManager: Tool interface for reading and mutating the roadmap.

LLMs call these methods — they never write plan YAML directly.
Every mutation validates invariants before persisting.

The manager enforces:
- Valid milestone status transitions
- Dependency satisfaction before status advancement
- Capability uniqueness and referential integrity
- Timestamps on state changes
- Schema conformance on every write
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    Capability,
    CapabilityStatus,
    Milestone,
    MilestoneStatus,
    MILESTONE_TRANSITIONS,
    Roadmap,
    validate_roadmap,
)

logger = logging.getLogger(__name__)


class PlanError(Exception):
    """Raised when a plan mutation would violate invariants."""


class PlanManager:
    """
    Tool interface for the roadmap.

    All mutations go through this class. It validates before writing
    and provides query methods for the PA's planning context.

    Usage:
        pm = PlanManager("plan/roadmap.yaml")
        roadmap = pm.load()

        pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
        pm.add_capability("m3-coding-agent", Capability(id="git-workspace", ...))
        pm.complete_capability("task-bundle-contracts")

        context = pm.planning_context()  # PA reads this during plan_work()
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._roadmap: Roadmap | None = None

    def load(self) -> Roadmap:
        """Load roadmap from YAML. Validates on load."""
        if not self.path.exists():
            raise PlanError(f"Roadmap file not found: {self.path}")

        with open(self.path) as f:
            data = yaml.safe_load(f)

        self._roadmap = Roadmap.model_validate(data)
        return self._roadmap

    def save(self) -> None:
        """Persist roadmap to YAML. Validates before writing."""
        if not self._roadmap:
            raise PlanError("No roadmap loaded")

        # Validate before writing
        errors = validate_roadmap(self._roadmap)
        if errors:
            raise PlanError(f"Cannot save — validation errors: {'; '.join(errors)}")

        self._roadmap.updated_at = datetime.now(UTC)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            yaml.dump(
                self._roadmap.model_dump(mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        logger.info("Roadmap saved to %s", self.path)

    @property
    def roadmap(self) -> Roadmap:
        if not self._roadmap:
            raise PlanError("No roadmap loaded. Call load() first.")
        return self._roadmap

    # ------------------------------------------------------------------
    # Milestone mutations (validated)
    # ------------------------------------------------------------------

    def advance_milestone(self, milestone_id: str, new_status: MilestoneStatus) -> Milestone:
        """
        Transition a milestone to a new status.

        Validates:
        - Milestone exists
        - Transition is valid (see MILESTONE_TRANSITIONS)
        - Dependencies are satisfied (for IN_PROGRESS)
        """
        milestone = self.roadmap.get_milestone(milestone_id)
        if not milestone:
            raise PlanError(f"Milestone '{milestone_id}' not found")

        # Check valid transition
        allowed = MILESTONE_TRANSITIONS.get(milestone.status, set())
        if new_status not in allowed:
            raise PlanError(
                f"Cannot transition milestone '{milestone_id}' from "
                f"'{milestone.status.value}' to '{new_status.value}'. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        # Check dependencies for IN_PROGRESS
        if new_status == MilestoneStatus.IN_PROGRESS:
            for dep_id in milestone.depends_on:
                dep = self.roadmap.get_milestone(dep_id)
                if dep and dep.status != MilestoneStatus.COMPLETE:
                    raise PlanError(
                        f"Cannot start milestone '{milestone_id}' — "
                        f"dependency '{dep_id}' is '{dep.status.value}', "
                        f"not complete"
                    )

        # Check all capabilities complete for COMPLETE
        if new_status == MilestoneStatus.COMPLETE:
            incomplete = [
                c.id for c in milestone.capabilities
                if c.status != CapabilityStatus.COMPLETE
            ]
            if incomplete:
                raise PlanError(
                    f"Cannot complete milestone '{milestone_id}' — "
                    f"capabilities not complete: {incomplete}"
                )

        # Apply transition
        old_status = milestone.status
        milestone.status = new_status

        if new_status == MilestoneStatus.IN_PROGRESS and not milestone.started_at:
            milestone.started_at = datetime.now(UTC)
        elif new_status == MilestoneStatus.COMPLETE:
            milestone.completed_at = datetime.now(UTC)

        self.save()
        logger.info(
            "Milestone '%s': %s → %s",
            milestone_id,
            old_status.value,
            new_status.value,
        )

        return milestone

    # ------------------------------------------------------------------
    # Capability mutations (validated)
    # ------------------------------------------------------------------

    def add_capability(
        self,
        milestone_id: str,
        capability: Capability,
    ) -> Capability:
        """
        Add a capability to a milestone.

        Validates:
        - Milestone exists
        - Capability ID is unique across all milestones
        - Capability dependencies reference existing capabilities
        """
        milestone = self.roadmap.get_milestone(milestone_id)
        if not milestone:
            raise PlanError(f"Milestone '{milestone_id}' not found")

        # Check uniqueness
        existing = self.roadmap.get_capability(capability.id)
        if existing:
            raise PlanError(
                f"Capability '{capability.id}' already exists in "
                f"milestone '{existing[0].id}'"
            )

        # Check dependency references
        all_cap_ids = {c.id for _, c in self.roadmap.all_capabilities()}
        for dep in capability.depends_on:
            if dep not in all_cap_ids:
                raise PlanError(
                    f"Capability '{capability.id}' depends on "
                    f"'{dep}' which does not exist"
                )

        milestone.capabilities.append(capability)
        self.save()
        logger.info(
            "Added capability '%s' to milestone '%s'",
            capability.id,
            milestone_id,
        )

        return capability

    def advance_capability(
        self,
        capability_id: str,
        new_status: CapabilityStatus,
    ) -> Capability:
        """
        Update a capability's status.

        Validates:
        - Capability exists
        - Parent milestone is in_progress (can't work on planned milestones)
        - Dependencies are satisfied (for in_progress and complete)
        """
        result = self.roadmap.get_capability(capability_id)
        if not result:
            raise PlanError(f"Capability '{capability_id}' not found")

        milestone, capability = result

        # Can't work on capabilities in non-active milestones
        if new_status == CapabilityStatus.IN_PROGRESS:
            if milestone.status != MilestoneStatus.IN_PROGRESS:
                raise PlanError(
                    f"Cannot start capability '{capability_id}' — "
                    f"milestone '{milestone.id}' is "
                    f"'{milestone.status.value}', not in_progress"
                )

        # Check capability dependencies for completion
        if new_status == CapabilityStatus.COMPLETE:
            for dep_id in capability.depends_on:
                dep_result = self.roadmap.get_capability(dep_id)
                if dep_result:
                    _, dep_cap = dep_result
                    if dep_cap.status != CapabilityStatus.COMPLETE:
                        raise PlanError(
                            f"Cannot complete capability '{capability_id}' — "
                            f"dependency '{dep_id}' is '{dep_cap.status.value}'"
                        )

        old_status = capability.status
        capability.status = new_status
        self.save()
        logger.info(
            "Capability '%s': %s → %s",
            capability_id,
            old_status.value,
            new_status.value,
        )

        return capability

    def link_spec(self, capability_id: str, spec_ref: str) -> None:
        """Link an IDD spec artifact to a capability."""
        result = self.roadmap.get_capability(capability_id)
        if not result:
            raise PlanError(f"Capability '{capability_id}' not found")

        _, capability = result
        if spec_ref not in capability.spec_refs:
            capability.spec_refs.append(spec_ref)
            self.save()

    def link_issue(self, capability_id: str, issue_ref: str) -> None:
        """Link a GitHub issue to a capability."""
        result = self.roadmap.get_capability(capability_id)
        if not result:
            raise PlanError(f"Capability '{capability_id}' not found")

        _, capability = result
        if issue_ref not in capability.issue_refs:
            capability.issue_refs.append(issue_ref)
            self.save()

    # ------------------------------------------------------------------
    # Milestone structure mutations
    # ------------------------------------------------------------------

    def add_milestone(self, milestone: Milestone) -> Milestone:
        """
        Add a milestone to the roadmap.

        Validates:
        - ID is unique
        - Dependencies reference existing milestones
        """
        if self.roadmap.get_milestone(milestone.id):
            raise PlanError(f"Milestone '{milestone.id}' already exists")

        existing_ids = set(self.roadmap.milestone_ids())
        for dep in milestone.depends_on:
            if dep not in existing_ids:
                raise PlanError(
                    f"Milestone '{milestone.id}' depends on "
                    f"'{dep}' which does not exist"
                )

        self.roadmap.milestones.append(milestone)
        self.save()
        logger.info("Added milestone '%s'", milestone.id)

        return milestone

    # ------------------------------------------------------------------
    # Query methods (for PA planning context)
    # ------------------------------------------------------------------

    def planning_context(self) -> dict[str, Any]:
        """
        Generate a summary for the PA's planning node.

        This is what the PA reads during plan_work() to understand
        where things stand before dispatching tasks.
        """
        roadmap = self.roadmap

        completed = [m for m in roadmap.milestones if m.status == MilestoneStatus.COMPLETE]
        active = roadmap.active_milestones()
        available = roadmap.available_milestones()
        blocked = [m for m in roadmap.milestones if m.status == MilestoneStatus.BLOCKED]

        # Capabilities summary
        all_caps = roadmap.all_capabilities()
        caps_complete = sum(1 for _, c in all_caps if c.status == CapabilityStatus.COMPLETE)
        caps_total = len(all_caps)

        return {
            "roadmap": roadmap.name,
            "progress": f"{caps_complete}/{caps_total} capabilities complete",
            "completed_milestones": [m.id for m in completed],
            "active_milestones": [
                {
                    "id": m.id,
                    "name": m.name,
                    "capabilities": [
                        {"id": c.id, "name": c.name, "status": c.status.value}
                        for c in m.capabilities
                    ],
                }
                for m in active
            ],
            "available_milestones": [
                {"id": m.id, "name": m.name, "description": m.description}
                for m in available
            ],
            "blocked_milestones": [
                {"id": m.id, "blocked_by": m.depends_on}
                for m in blocked
            ],
        }

    def what_can_start(self) -> list[Capability]:
        """
        Which capabilities can be started right now?

        A capability can start if:
        - Its milestone is in_progress
        - Its dependencies are all complete
        - It is not_started
        """
        startable = []
        for milestone in self.roadmap.active_milestones():
            for cap in milestone.capabilities:
                if cap.status != CapabilityStatus.NOT_STARTED:
                    continue

                deps_met = True
                for dep_id in cap.depends_on:
                    dep_result = self.roadmap.get_capability(dep_id)
                    if dep_result:
                        _, dep_cap = dep_result
                        if dep_cap.status != CapabilityStatus.COMPLETE:
                            deps_met = False
                            break

                if deps_met:
                    startable.append(cap)

        return startable
