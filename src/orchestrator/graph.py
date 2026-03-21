"""
Orchestrator Graph: The Primary Agent as a LangGraph StateGraph.

This is the PM — it receives stakeholder requests, plans work,
dispatches TaskBundles to agents, and manages the lifecycle:
dispatch → monitor → evaluate → route next.

The graph checkpoints to Postgres, so it survives restarts and
can resume when agents report back via callbacks.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, StateGraph

from src.contracts import (
    CodingBundle,
    TaskStatus,
)
from src.orchestrator.state import OrchestratorState, TaskRecord

logger = logging.getLogger(__name__)

# The PA's LLM — used for planning, evaluation, and stakeholder communication
llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=16000)


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------


def plan_work(state: OrchestratorState) -> dict[str, Any]:
    """
    PA analyzes the stakeholder request and decides what to do.

    Reads context from JIRA/Confluence (via state.context), breaks down
    the work, and prepares TaskBundles for dispatch.
    """
    messages = state["messages"]
    context = state.get("context", {})

    # Ask the LLM to plan
    planning_prompt = (
        "You are a project manager orchestrating software development agents. "
        "Based on the stakeholder request and project context, determine what "
        "tasks need to be done and which agents should handle them.\n\n"
        f"Project context: {context}\n\n"
        "Respond with a structured plan including:\n"
        "1. What the coding agent should build\n"
        "2. Acceptance criteria\n"
        "3. Any testing requirements\n"
    )

    response = llm.invoke(
        [{"role": "system", "content": planning_prompt}] + messages
    )

    return {"messages": [response]}


def dispatch_coding(state: OrchestratorState) -> dict[str, Any]:
    """
    Dispatch a CodingBundle to the Coding Agent.

    Creates the bundle, records it in state, and sends it.
    The graph then interrupts, waiting for the agent to report back.
    """
    # In a real implementation, this would:
    # 1. Build the CodingBundle from the plan
    # 2. POST it to the Coding Agent's endpoint
    # 3. Record the task in state
    # For now, we create the record and simulate dispatch

    story = state.get("current_story", {}) or {}

    bundle = CodingBundle(
        objective=story.get("objective", "Implement feature"),
        context=story.get("context", ""),
        acceptance_criteria=story.get("acceptance_criteria", []),
        callback_url="http://localhost:8000/callback",  # PA's callback endpoint
        repo_url=story.get("repo_url", ""),
        base_branch="main",
        draft_pr=True,
    )

    task_record = TaskRecord(
        task_id=bundle.task_id,
        agent_type="coding",
        bundle=bundle.model_dump(mode="json"),
        status=TaskStatus.DISPATCHED.value,
        last_update=None,
        result=None,
        pr_url=None,
    )

    logger.info("Dispatched coding task %s: %s", bundle.task_id, bundle.objective)

    return {"tasks": [task_record]}


def evaluate_result(state: OrchestratorState) -> dict[str, Any]:
    """
    PA evaluates the result from an agent.

    Checks if the task succeeded, if the PR looks good, and decides
    what to do next: merge → UAT, request changes, or escalate.
    """
    tasks = state.get("tasks", [])
    latest_completed = next(
        (t for t in reversed(tasks) if t["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)),
        None,
    )

    if not latest_completed:
        return {"messages": [{"role": "assistant", "content": "No completed tasks to evaluate."}]}

    result = latest_completed.get("result", {})
    if result and result.get("success"):
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"Task {latest_completed['task_id']} completed successfully. "
                        f"Summary: {result.get('summary', 'N/A')}. "
                        f"PR: {latest_completed.get('pr_url', 'N/A')}. "
                        "Ready to proceed with PR review and UAT."
                    ),
                }
            ]
        }
    else:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"Task {latest_completed['task_id']} failed. "
                        f"Errors: {result.get('errors', []) if result else 'Unknown'}. "
                        "Evaluating whether to retry or escalate."
                    ),
                }
            ]
        }


def route_after_evaluation(state: OrchestratorState) -> Literal["dispatch_coding", "__end__"]:
    """
    Routing logic after evaluating a result.

    Decides whether to dispatch more work or end the flow.
    """
    tasks = state.get("tasks", [])
    latest = next(
        (t for t in reversed(tasks) if t["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)),
        None,
    )

    if latest and latest.get("result", {}).get("success"):
        # Success — in full implementation, would route to PR review → UAT → deploy
        return "__end__"
    elif latest and latest["status"] == TaskStatus.FAILED.value:
        # Failed — could retry or escalate. For now, end.
        return "__end__"
    else:
        return "__end__"


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------


def build_orchestrator_graph() -> StateGraph:
    """
    Build the PA's StateGraph.

    Flow:
        START → plan_work → dispatch_coding → (interrupt: wait for agent)
              → evaluate_result → route → (next agent or END)
    """
    graph = StateGraph(OrchestratorState)

    # Add nodes
    graph.add_node("plan_work", plan_work)
    graph.add_node("dispatch_coding", dispatch_coding)
    graph.add_node("evaluate_result", evaluate_result)

    # Add edges
    graph.add_edge(START, "plan_work")
    graph.add_edge("plan_work", "dispatch_coding")
    graph.add_edge("dispatch_coding", "evaluate_result")
    graph.add_conditional_edges("evaluate_result", route_after_evaluation)

    return graph
