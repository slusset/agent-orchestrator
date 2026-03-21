"""
Orchestrator State: The PA's view of the world.

This TypedDict is the shared state that flows through the LangGraph StateGraph.
Each node (agent dispatcher, watchdog, evaluator) reads and writes to this state.
LangGraph checkpoints it to Postgres automatically.
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph import add_messages
from typing_extensions import TypedDict

from src.contracts.task_bundle import StatusUpdate, TaskBundle, TaskResult, TaskStatus


class TaskRecord(TypedDict):
    """PA's record of a dispatched task. Stored in orchestrator state."""

    task_id: str
    agent_type: str  # "coding", "uat", "devops", "pr"
    bundle: dict[str, Any]  # Serialized TaskBundle
    status: str  # TaskStatus value
    last_update: dict[str, Any] | None  # Most recent StatusUpdate
    result: dict[str, Any] | None  # TaskResult when complete
    pr_url: str | None  # Set when a PR is created


def merge_tasks(existing: list[TaskRecord], new: list[TaskRecord]) -> list[TaskRecord]:
    """
    LangGraph reducer: merge task records by task_id.
    New records update existing ones; unknown task_ids are appended.
    """
    by_id = {t["task_id"]: t for t in existing}
    for task in new:
        by_id[task["task_id"]] = task
    return list(by_id.values())


class OrchestratorState(TypedDict):
    """
    Root state for the PA's StateGraph.

    - messages: conversation history with the stakeholder (LangGraph managed)
    - tasks: all dispatched tasks and their current status
    - current_story: the active story/feature being worked on
    - context: project-level context pulled from JIRA/Confluence/project data
    """

    messages: Annotated[list, add_messages]
    tasks: Annotated[list[TaskRecord], merge_tasks]
    current_story: dict[str, Any] | None
    context: dict[str, Any]
