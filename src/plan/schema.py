"""
Plan Schema: Formal definition of the roadmap/planning layer.

This is the strategic planning layer that sits ABOVE specs and BELOW vision.
The PA consults this for context during planning — agents never see it.

The schema enforces:
- Valid milestone lifecycle transitions
- Dependency integrity (no circular deps, no deps on nonexistent milestones)
- Capability-to-milestone uniqueness (a capability belongs to exactly one milestone)
- Required fields and allowed values

LLMs should NEVER edit plan YAML directly. They call plan tools
(PlanManager) which validate invariants before writing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MilestoneStatus(str, Enum):
    """Lifecycle states of a milestone."""

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    DEFERRED = "deferred"


# Valid transitions — enforced by PlanManager, not by raw YAML edits
MILESTONE_TRANSITIONS: dict[MilestoneStatus, set[MilestoneStatus]] = {
    MilestoneStatus.PLANNED: {MilestoneStatus.IN_PROGRESS, MilestoneStatus.DEFERRED},
    MilestoneStatus.IN_PROGRESS: {MilestoneStatus.COMPLETE, MilestoneStatus.BLOCKED, MilestoneStatus.DEFERRED},
    MilestoneStatus.BLOCKED: {MilestoneStatus.IN_PROGRESS, MilestoneStatus.DEFERRED},
    MilestoneStatus.COMPLETE: set(),  # Terminal
    MilestoneStatus.DEFERRED: {MilestoneStatus.PLANNED},  # Can re-plan
}


class CapabilityStatus(str, Enum):
    """Status of an individual capability within a milestone."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    DEFERRED = "deferred"


class Capability(BaseModel):
    """
    A discrete unit of functionality within a milestone.

    Capabilities map to IDD specs (narratives, features, contracts)
    and to GitHub issues for tracking. They are the bridge between
    strategic planning and tactical execution.
    """

    id: str  # kebab-case identifier
    name: str  # Human-readable
    description: str = ""
    status: CapabilityStatus = CapabilityStatus.NOT_STARTED

    # Traceability
    spec_refs: list[str] = Field(
        default_factory=list,
        description="Paths to IDD spec artifacts (narratives, features, etc.)",
    )
    issue_refs: list[str] = Field(
        default_factory=list,
        description="GitHub issue references (e.g., '#2', 'org/repo#42')",
    )

    # Dependencies within the milestone
    depends_on: list[str] = Field(
        default_factory=list,
        description="IDs of capabilities this depends on (within any milestone)",
    )


class Milestone(BaseModel):
    """
    A strategic phase of work with a cohesive goal.

    Milestones group capabilities and express sequencing constraints.
    The PA uses milestones to understand "where are we" and
    "what can we start next."
    """

    id: str  # kebab-case identifier
    name: str  # Human-readable
    description: str = ""
    status: MilestoneStatus = MilestoneStatus.PLANNED

    # Sequencing
    depends_on: list[str] = Field(
        default_factory=list,
        description="IDs of milestones that must be complete before this starts",
    )

    # Contents
    capabilities: list[Capability] = Field(default_factory=list)

    # Dates (optional, for tracking)
    target_date: date | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Success criteria — what "done" means for this milestone
    success_criteria: list[str] = Field(default_factory=list)

    def capability_ids(self) -> list[str]:
        return [c.id for c in self.capabilities]

    def get_capability(self, cap_id: str) -> Capability | None:
        return next((c for c in self.capabilities if c.id == cap_id), None)

    def all_capabilities_complete(self) -> bool:
        return all(c.status == CapabilityStatus.COMPLETE for c in self.capabilities)


class Roadmap(BaseModel):
    """
    The top-level strategic plan.

    Contains milestones in priority order. The PA reads this
    during planning to understand context, dependencies, and
    what to work on next.
    """

    id: str
    name: str
    description: str = ""
    version: str = "0.1.0"

    milestones: list[Milestone] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_integrity(self) -> Roadmap:
        """Validate structural integrity of the roadmap."""
        errors = validate_roadmap(self)
        if errors:
            raise ValueError(f"Roadmap validation failed: {'; '.join(errors)}")
        return self

    def get_milestone(self, milestone_id: str) -> Milestone | None:
        return next((m for m in self.milestones if m.id == milestone_id), None)

    def milestone_ids(self) -> list[str]:
        return [m.id for m in self.milestones]

    def active_milestones(self) -> list[Milestone]:
        return [m for m in self.milestones if m.status == MilestoneStatus.IN_PROGRESS]

    def available_milestones(self) -> list[Milestone]:
        """Milestones whose dependencies are all complete."""
        complete_ids = {m.id for m in self.milestones if m.status == MilestoneStatus.COMPLETE}
        return [
            m for m in self.milestones
            if m.status == MilestoneStatus.PLANNED
            and all(dep in complete_ids for dep in m.depends_on)
        ]

    def get_capability(self, cap_id: str) -> tuple[Milestone, Capability] | None:
        """Find a capability by ID across all milestones."""
        for m in self.milestones:
            cap = m.get_capability(cap_id)
            if cap:
                return m, cap
        return None

    def all_capabilities(self) -> list[tuple[str, Capability]]:
        """All capabilities with their milestone ID."""
        result = []
        for m in self.milestones:
            for c in m.capabilities:
                result.append((m.id, c))
        return result


# ---------------------------------------------------------------------------
# Validation functions (used by schema and by CI tooling)
# ---------------------------------------------------------------------------


def validate_roadmap(roadmap: Roadmap) -> list[str]:
    """
    Validate roadmap integrity. Returns list of error strings.
    Empty list = valid.
    """
    errors: list[str] = []
    milestone_ids = {m.id for m in roadmap.milestones}
    all_cap_ids: dict[str, str] = {}  # cap_id → milestone_id

    for milestone in roadmap.milestones:
        # Check milestone dependencies exist
        for dep in milestone.depends_on:
            if dep not in milestone_ids:
                errors.append(
                    f"Milestone '{milestone.id}' depends on "
                    f"'{dep}' which does not exist"
                )

        # Check for duplicate capability IDs across milestones
        for cap in milestone.capabilities:
            if cap.id in all_cap_ids:
                errors.append(
                    f"Capability '{cap.id}' appears in both "
                    f"'{all_cap_ids[cap.id]}' and '{milestone.id}'"
                )
            all_cap_ids[cap.id] = milestone.id

            # Check capability dependencies exist
            for dep in cap.depends_on:
                # Will be checked after all caps are collected
                pass

    # Check capability dependencies reference valid capability IDs
    for milestone in roadmap.milestones:
        for cap in milestone.capabilities:
            for dep in cap.depends_on:
                if dep not in all_cap_ids:
                    errors.append(
                        f"Capability '{cap.id}' depends on "
                        f"'{dep}' which does not exist"
                    )

    # Check for circular milestone dependencies
    circular = _detect_circular_deps(
        {m.id: m.depends_on for m in roadmap.milestones}
    )
    if circular:
        errors.append(f"Circular milestone dependencies detected: {circular}")

    # Check milestone status consistency
    for milestone in roadmap.milestones:
        if milestone.status == MilestoneStatus.IN_PROGRESS:
            # Dependencies must be complete
            for dep in milestone.depends_on:
                dep_milestone = roadmap.get_milestone(dep)
                if dep_milestone and dep_milestone.status != MilestoneStatus.COMPLETE:
                    errors.append(
                        f"Milestone '{milestone.id}' is in_progress but "
                        f"dependency '{dep}' is '{dep_milestone.status.value}'"
                    )

    return errors


def _detect_circular_deps(deps: dict[str, list[str]]) -> list[str] | None:
    """Detect circular dependencies using DFS. Returns cycle path or None."""
    visited: set[str] = set()
    in_stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> bool:
        visited.add(node)
        in_stack.add(node)
        path.append(node)

        for dep in deps.get(node, []):
            if dep not in visited:
                if dfs(dep):
                    return True
            elif dep in in_stack:
                cycle_start = path.index(dep)
                path.append(dep)
                return True

        path.pop()
        in_stack.discard(node)
        return False

    for node in deps:
        if node not in visited:
            if dfs(node):
                return path

    return None
