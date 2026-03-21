"""
TaskBundle: The hand-off contract between the Primary Agent and specialized agents.

The TaskBundle is transport-agnostic — it defines WHAT gets communicated,
not HOW. Transport (HTTP callback, websocket, queue) is handled separately
by the StatusReporter abstraction.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Lifecycle states of a task."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class StatusUpdate(BaseModel):
    """What an agent posts back to the PA via callback."""

    task_id: str
    status: TaskStatus
    message: str = ""
    progress_pct: int | None = Field(None, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TaskResult(BaseModel):
    """Completion payload — attached to the final status update."""

    task_id: str
    success: bool
    summary: str
    artifacts: list[str] = Field(default_factory=list)  # PR URLs, file paths, etc.
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class TaskBundle(BaseModel):
    """
    Base hand-off contract from the Primary Agent to any specialized agent.

    The bundle carries everything an agent needs to do its work independently:
    - What to do (objective, context, acceptance criteria)
    - How to report back (callback_url, status_interval)
    - What skills/tools are available
    - Timeout and priority constraints

    Subclasses add agent-specific fields (e.g., repo info for CodingBundle,
    environment targets for DevOpsBundle).
    """

    # Identity
    task_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    parent_task_id: str | None = None  # For sub-tasks
    story_id: str | None = None  # JIRA/external tracker reference

    # What to do
    objective: str  # Clear, actionable description
    context: str = ""  # Background info, specs, relevant history
    acceptance_criteria: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)  # Skills agent should use

    # Communication
    callback_url: str  # PA's webhook endpoint for status updates
    status_interval: int = Field(default=300, description="Seconds between heartbeats")

    # Constraints
    priority: TaskPriority = TaskPriority.MEDIUM
    timeout_minutes: int = Field(default=60, description="Max time before PA escalates")

    # Metadata
    dispatched_at: datetime = Field(default_factory=datetime.utcnow)
    dispatched_by: str = "primary_agent"
    metadata: dict[str, Any] = Field(default_factory=dict)
