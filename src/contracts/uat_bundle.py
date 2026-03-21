"""
UATBundle: TaskBundle specialized for the UAT Agent.

The UAT Agent validates work against specs, user journeys, and acceptance
criteria. It reviews what's in the repo (post-merge or on a branch),
not direct output from the Coding Agent.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .task_bundle import TaskBundle


class UATProfile(str, Enum):
    """Testing profile determines the UAT Agent's approach."""

    SCRIPTED = "scripted"  # Run predetermined test scripts
    EXPLORATORY = "exploratory"  # Explore based on specs/journeys
    REGRESSION = "regression"  # Validate nothing broke
    ACCESSIBILITY = "accessibility"  # A11y focused


class UATBundle(TaskBundle):
    """
    Hand-off contract for the UAT Agent.

    The agent validates against specs, user stories, and journeys.
    It operates on the repo state (branch or post-merge), not on
    direct output from the Coding Agent.
    """

    # What to validate
    repo_url: str
    branch: str  # Branch or ref to test against
    profile: UATProfile = UATProfile.EXPLORATORY

    # Specs and context
    user_stories: list[str] = Field(default_factory=list)  # JIRA IDs or descriptions
    journey_specs: list[str] = Field(default_factory=list)  # Paths to journey files
    test_scripts: list[str] = Field(default_factory=list)  # Paths to predetermined scripts

    # Environment
    test_environment_url: str | None = None  # If testing a deployed instance
    environment: str = "uat"  # dev, uat, staging

    # Expectations
    fail_fast: bool = False  # Stop on first failure or run all
    screenshot_on_failure: bool = True
