"""
Shared test fixtures for the agent-orchestrator test suite.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Capability: specs/capabilities/agent-orchestration.capability.yaml
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.plan.schema import (
    Capability,
    CapabilityStatus,
    Milestone,
    MilestoneStatus,
    Roadmap,
)


@pytest.fixture
def sample_roadmap_data() -> dict:
    """A valid roadmap for testing."""
    return {
        "id": "test-roadmap",
        "name": "Test Roadmap",
        "description": "A test roadmap",
        "version": "0.1.0",
        "milestones": [
            {
                "id": "m1-foundation",
                "name": "Foundation",
                "status": "complete",
                "depends_on": [],
                "capabilities": [
                    {
                        "id": "contracts",
                        "name": "Task contracts",
                        "status": "complete",
                    },
                    {
                        "id": "graph",
                        "name": "Orchestrator graph",
                        "status": "complete",
                        "depends_on": ["contracts"],
                    },
                ],
                "success_criteria": ["All contracts import"],
            },
            {
                "id": "m2-invocation",
                "name": "Invocation",
                "status": "planned",
                "depends_on": ["m1-foundation"],
                "capabilities": [
                    {
                        "id": "callback-server",
                        "name": "Callback server",
                        "status": "not_started",
                    },
                    {
                        "id": "http-dispatch",
                        "name": "HTTP dispatch",
                        "status": "not_started",
                        "depends_on": ["callback-server"],
                    },
                ],
            },
            {
                "id": "m3-agents",
                "name": "Agent implementations",
                "status": "planned",
                "depends_on": ["m2-invocation"],
                "capabilities": [
                    {
                        "id": "coding-agent",
                        "name": "Coding agent",
                        "status": "not_started",
                    },
                ],
            },
        ],
    }


@pytest.fixture
def sample_roadmap(sample_roadmap_data) -> Roadmap:
    """A validated Roadmap instance."""
    return Roadmap.model_validate(sample_roadmap_data)


@pytest.fixture
def roadmap_file(sample_roadmap_data, tmp_path) -> Path:
    """Write a roadmap to a temp file and return the path."""
    path = tmp_path / "roadmap.yaml"
    with open(path, "w") as f:
        yaml.dump(sample_roadmap_data, f)
    return path
