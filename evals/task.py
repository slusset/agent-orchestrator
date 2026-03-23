"""
Eval Task: Definition and loading of evaluation tasks.

Each task is a directory containing:
    task.yaml       — task definition (maps to CodingBundle fields)
    seed-repo/      — a git repository with failing tests
    verify          — optional custom verification script

The seed repo must:
    1. Be a valid git repo (has .git/ or can be git init'd)
    2. Have tests that FAIL before the agent works
    3. Have tests that PASS after correct implementation

This is the SWE-bench pattern scaled down to purpose-built tasks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class EvalTask:
    """
    A single evaluation task.

    Maps closely to CodingBundle but adds eval-specific fields
    (expected outcomes, difficulty, tags).
    """

    # Identity
    task_id: str
    name: str
    description: str = ""

    # What the agent receives
    objective: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    context: str = ""

    # Repo
    seed_repo: Path = field(default_factory=lambda: Path("."))
    base_branch: str = "main"
    focus_paths: list[str] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)

    # Verification
    verify_command: str = "pytest tests/ -v"
    fail_to_pass_tests: list[str] = field(default_factory=list)  # Tests that should go green
    pass_to_pass_tests: list[str] = field(default_factory=list)  # Tests that must stay green

    # Test configuration
    test_frameworks: list[str] = field(default_factory=lambda: ["pytest"])
    run_unit_tests: bool = True

    # Eval metadata
    difficulty: str = "easy"  # easy | medium | hard
    tags: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)  # Files the agent should modify/create
    max_time_seconds: float = 300.0  # Time budget

    # Source (for Tier 2: real repo evals)
    source_repo: str | None = None  # e.g., "github.com/org/repo"
    source_issue: str | None = None  # e.g., "#123"
    source_pr: str | None = None  # e.g., "#456"

    @classmethod
    def from_yaml(cls, path: Path) -> EvalTask:
        """Load a task from a task.yaml file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        # Resolve seed_repo relative to the task.yaml's directory
        task_dir = path.parent
        seed_repo = task_dir / data.get("seed_repo", "seed-repo")

        return cls(
            task_id=data["task_id"],
            name=data["name"],
            description=data.get("description", ""),
            objective=data.get("objective", data["name"]),
            acceptance_criteria=data.get("acceptance_criteria", []),
            context=data.get("context", ""),
            seed_repo=seed_repo,
            base_branch=data.get("base_branch", "main"),
            focus_paths=data.get("focus_paths", []),
            protected_paths=data.get("protected_paths", []),
            verify_command=data.get("verify_command", "pytest tests/ -v"),
            fail_to_pass_tests=data.get("fail_to_pass_tests", []),
            pass_to_pass_tests=data.get("pass_to_pass_tests", []),
            test_frameworks=data.get("test_frameworks", ["pytest"]),
            run_unit_tests=data.get("run_unit_tests", True),
            difficulty=data.get("difficulty", "easy"),
            tags=data.get("tags", []),
            expected_files=data.get("expected_files", []),
            max_time_seconds=data.get("max_time_seconds", 300.0),
            source_repo=data.get("source_repo"),
            source_issue=data.get("source_issue"),
            source_pr=data.get("source_pr"),
        )

    def validate(self) -> list[str]:
        """Check that the task is well-formed. Returns list of issues."""
        issues = []

        if not self.task_id:
            issues.append("task_id is required")
        if not self.name:
            issues.append("name is required")
        if not self.objective:
            issues.append("objective is required")
        if not self.seed_repo.exists():
            issues.append(f"seed_repo does not exist: {self.seed_repo}")
        if not self.acceptance_criteria:
            issues.append("acceptance_criteria should not be empty")

        return issues


def discover_tasks(tasks_dir: Path) -> list[EvalTask]:
    """
    Discover all eval tasks in a directory.

    Looks for task.yaml files in immediate subdirectories.
    """
    tasks = []

    if not tasks_dir.exists():
        logger.warning("Tasks directory does not exist: %s", tasks_dir)
        return tasks

    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue

        task_yaml = task_dir / "task.yaml"
        if not task_yaml.exists():
            continue

        try:
            task = EvalTask.from_yaml(task_yaml)
            issues = task.validate()
            if issues:
                logger.warning("Task %s has issues: %s", task.task_id, issues)
            else:
                tasks.append(task)
        except Exception as e:
            logger.error("Failed to load task from %s: %s", task_yaml, e)

    logger.info("Discovered %d eval tasks in %s", len(tasks), tasks_dir)
    return tasks
