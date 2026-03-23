"""
Orchestrator Graph: The Primary Agent as a LangGraph StateGraph.

This is the PM — it receives stakeholder requests, plans work,
dispatches TaskBundles to agents, and manages the lifecycle:
dispatch → interrupt (wait for callback) → evaluate → route next.

The graph checkpoints to Postgres, so it survives restarts and
can resume when agents report back via callbacks.

Key pattern: Agents are NOT nodes in this graph. They run as separate
processes. The graph dispatches TaskBundles and then INTERRUPTS,
waiting for an external callback to resume it with a TaskResult.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.contracts import (
    CodingBundle,
    PRBundle,
    PRAction,
    TaskStatus,
    UATBundle,
)
from src.orchestrator.state import OrchestratorState, TaskRecord

logger = logging.getLogger(__name__)

# The PA's LLM — used for planning, evaluation, and stakeholder communication
llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=16000)


def _resolved_env_for_role(context: dict[str, Any], role: str) -> dict[str, str]:
    """Extract resolved credentials from graph context, filtered by role."""
    all_creds: dict[str, dict[str, str]] = context.get("_resolved_credentials", {})
    return dict(all_creds.get(role, {}))


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------


def plan_work(state: OrchestratorState) -> dict[str, Any]:
    """
    PA analyzes the stakeholder request and decides what to do.

    Reads strategic context from the roadmap (plan layer) and
    project context from JIRA/Confluence (via state.context).
    Uses both to plan work and prepare TaskBundles for dispatch.

    The roadmap tells the PA:
    - Where we are in the overall plan
    - What milestones are active / available
    - Which capabilities can be started
    - What dependencies must be satisfied first
    """
    messages = state["messages"]
    context = state.get("context", {})

    # Load strategic context from the plan layer
    plan_context = {}
    plan_path = context.get("plan_path", "plan/roadmap.yaml")
    try:
        from src.plan.manager import PlanManager

        pm = PlanManager(plan_path)
        pm.load()
        plan_context = pm.planning_context()
    except Exception as e:
        logger.warning("Could not load plan context: %s", e)

    planning_prompt = (
        "You are a project manager orchestrating software development agents. "
        "Based on the stakeholder request, project context, and the current "
        "roadmap status, determine what tasks need to be done and which "
        "agents should handle them.\n\n"
        f"Project context: {context}\n\n"
        f"Roadmap status: {plan_context}\n\n"
        "Use the roadmap to understand:\n"
        "- What has already been completed\n"
        "- What milestones are currently active\n"
        "- What capabilities are available to start\n"
        "- What dependencies exist\n\n"
        "Respond with a structured plan including:\n"
        "1. Which milestone/capability this work falls under\n"
        "2. What the coding agent should build\n"
        "3. Acceptance criteria\n"
        "4. Any testing requirements\n"
        "5. Whether any dependencies need to be completed first\n"
    )

    response = llm.invoke(
        [{"role": "system", "content": planning_prompt}] + messages
    )

    return {"messages": [response]}


def dispatch_coding(state: OrchestratorState) -> dict[str, Any]:
    """
    Build a CodingBundle and dispatch it to the Coding Agent.

    After recording the task in state, the graph moves to wait_for_agent
    which interrupts — the PA is now idle, waiting for the agent's callback.
    """
    story = state.get("current_story", {}) or {}
    context = state.get("context", {})

    bundle = CodingBundle(
        objective=story.get("objective", "Implement feature"),
        context=story.get("context", ""),
        acceptance_criteria=story.get("acceptance_criteria", []),
        story_id=story.get("story_id"),
        callback_url=context.get("callback_url", "http://localhost:8000/callback"),
        repo_url=story.get("repo_url", ""),
        base_branch="main",
        draft_pr=True,
        protected_paths=story.get("protected_paths", []),
        focus_paths=story.get("focus_paths", []),
        resolved_env=_resolved_env_for_role(context, "coding"),
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


def wait_for_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    INTERRUPT: Pause the graph until an agent reports back.

    LangGraph persists the state to the checkpoint store and stops
    execution here. When the PA's callback server receives a TaskResult,
    it resumes the graph by calling `graph.invoke(Command(resume=result), config)`.

    The interrupt() call returns whatever value is passed to Command(resume=...).
    In our case, that's the TaskResult dict from the callback handler.
    """
    # Find the most recently dispatched task
    tasks = state.get("tasks", [])
    active_task = next(
        (t for t in reversed(tasks) if t["status"] == TaskStatus.DISPATCHED.value),
        None,
    )

    task_id = active_task["task_id"] if active_task else "unknown"

    # This is the key line — graph execution STOPS here.
    # It resumes when Command(resume=<result_dict>) is called externally.
    agent_result = interrupt(
        {
            "reason": "waiting_for_agent",
            "task_id": task_id,
            "message": f"Waiting for agent to complete task {task_id}. "
            "Graph will resume when callback is received.",
        }
    )

    # --- Execution resumes here when the callback arrives ---
    # agent_result is whatever was passed to Command(resume=...)

    logger.info("Agent reported back for task %s", task_id)

    # Update the task record with the result
    pr_url = None
    if agent_result.get("artifacts"):
        pr_url = next(
            (a for a in agent_result["artifacts"] if "pull" in a or "/pr/" in a),
            None,
        )

    status = TaskStatus.COMPLETED.value if agent_result.get("success") else TaskStatus.FAILED.value

    updated_record = TaskRecord(
        task_id=task_id,
        agent_type=active_task["agent_type"] if active_task else "unknown",
        bundle=active_task["bundle"] if active_task else {},
        status=status,
        last_update=None,
        result=agent_result,
        pr_url=pr_url,
    )

    return {"tasks": [updated_record]}


def evaluate_result(state: OrchestratorState) -> dict[str, Any]:
    """
    PA evaluates the result from an agent.

    Uses the LLM to assess:
    - Did the task succeed?
    - Does the PR meet acceptance criteria?
    - What should happen next? (PR review → UAT → deploy, or retry/escalate)
    """
    tasks = state.get("tasks", [])
    latest_completed = next(
        (t for t in reversed(tasks)
         if t["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)),
        None,
    )

    if not latest_completed:
        return {"messages": [{"role": "assistant", "content": "No completed tasks to evaluate."}]}

    result = latest_completed.get("result", {})
    agent_type = latest_completed.get("agent_type", "unknown")

    if result and result.get("success"):
        msg = (
            f"✓ {agent_type.title()} task {latest_completed['task_id']} completed successfully.\n"
            f"Summary: {result.get('summary', 'N/A')}\n"
            f"PR: {latest_completed.get('pr_url', 'N/A')}\n"
        )
        # Determine next step based on which agent just completed
        if agent_type == "coding":
            msg += "Next: Route to PR Agent for review."
        elif agent_type == "pr":
            msg += "Next: Route to UAT Agent for validation."
        elif agent_type == "uat":
            msg += "Next: Route to DevOps Agent for deployment."
        elif agent_type == "devops":
            msg += "Deployment complete. Story ready to close."

        return {"messages": [{"role": "assistant", "content": msg}]}
    else:
        errors = result.get("errors", []) if result else ["Unknown error"]
        msg = (
            f"✗ {agent_type.title()} task {latest_completed['task_id']} failed.\n"
            f"Errors: {errors}\n"
            "Evaluating whether to retry or escalate to stakeholder."
        )
        return {"messages": [{"role": "assistant", "content": msg}]}


def route_after_evaluation(
    state: OrchestratorState,
) -> Literal["dispatch_pr_review", "dispatch_uat", "dispatch_devops", "handle_failure", "__end__"]:
    """
    Route to the next agent based on what just completed.

    Coding → PR Review → UAT → DevOps → END
    Any failure → handle_failure
    """
    tasks = state.get("tasks", [])
    latest = next(
        (t for t in reversed(tasks)
         if t["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)),
        None,
    )

    if not latest:
        return "__end__"

    if latest["status"] == TaskStatus.FAILED.value:
        return "handle_failure"

    agent_type = latest.get("agent_type", "")

    if agent_type == "coding":
        return "dispatch_pr_review"
    elif agent_type == "pr":
        return "dispatch_uat"
    elif agent_type == "uat":
        return "dispatch_devops"
    elif agent_type == "devops":
        return "__end__"  # Story complete
    else:
        return "__end__"


def dispatch_pr_review(state: OrchestratorState) -> dict[str, Any]:
    """Dispatch a PRBundle to the PR Agent after coding completes."""
    tasks = state.get("tasks", [])
    context = state.get("context", {})
    coding_task = next(
        (t for t in reversed(tasks) if t["agent_type"] == "coding" and t.get("pr_url")),
        None,
    )

    if not coding_task or not coding_task.get("pr_url"):
        logger.error("No PR URL found from coding task")
        return {"messages": [{"role": "assistant", "content": "Error: No PR to review."}]}

    # Extract PR number from URL (e.g., https://github.com/org/repo/pull/42 → 42)
    pr_url = coding_task["pr_url"]
    pr_number = int(pr_url.rstrip("/").split("/")[-1]) if pr_url else 0

    bundle = PRBundle(
        objective=f"Review PR #{pr_number}",
        callback_url=context.get("callback_url", "http://localhost:8000/callback"),
        repo_url=coding_task["bundle"].get("repo_url", ""),
        pr_number=pr_number,
        pr_url=pr_url,
        action=PRAction.REVIEW,
        acceptance_criteria=coding_task["bundle"].get("acceptance_criteria", []),
        parent_task_id=coding_task["task_id"],
        resolved_env=_resolved_env_for_role(context, "pr"),
    )

    task_record = TaskRecord(
        task_id=bundle.task_id,
        agent_type="pr",
        bundle=bundle.model_dump(mode="json"),
        status=TaskStatus.DISPATCHED.value,
        last_update=None,
        result=None,
        pr_url=pr_url,
    )

    logger.info("Dispatched PR review task %s for PR %s", bundle.task_id, pr_url)
    return {"tasks": [task_record]}


def dispatch_uat(state: OrchestratorState) -> dict[str, Any]:
    """Dispatch a UATBundle after PR is approved and merged."""
    tasks = state.get("tasks", [])
    story = state.get("current_story", {}) or {}
    context = state.get("context", {})

    coding_task = next(
        (t for t in reversed(tasks) if t["agent_type"] == "coding"),
        None,
    )

    bundle = UATBundle(
        objective="Validate feature against acceptance criteria",
        callback_url=context.get("callback_url", "http://localhost:8000/callback"),
        repo_url=story.get("repo_url", coding_task["bundle"].get("repo_url", "") if coding_task else ""),
        branch="main",  # Post-merge, validate on main
        user_stories=[story.get("story_id", "")] if story.get("story_id") else [],
        acceptance_criteria=story.get("acceptance_criteria", []),
        parent_task_id=coding_task["task_id"] if coding_task else None,
        resolved_env=_resolved_env_for_role(context, "uat"),
    )

    task_record = TaskRecord(
        task_id=bundle.task_id,
        agent_type="uat",
        bundle=bundle.model_dump(mode="json"),
        status=TaskStatus.DISPATCHED.value,
        last_update=None,
        result=None,
        pr_url=None,
    )

    logger.info("Dispatched UAT task %s", bundle.task_id)
    return {"tasks": [task_record]}


def dispatch_devops(state: OrchestratorState) -> dict[str, Any]:
    """Dispatch a DevOpsBundle after UAT passes."""
    from src.contracts import DevOpsBundle, DeployEnvironment, DeployAction

    story = state.get("current_story", {}) or {}
    context = state.get("context", {})

    bundle = DevOpsBundle(
        objective="Deploy validated feature to staging",
        callback_url=context.get("callback_url", "http://localhost:8000/callback"),
        repo_url=story.get("repo_url", ""),
        action=DeployAction.DEPLOY,
        target_environment=DeployEnvironment.STAGING,
        resolved_env=_resolved_env_for_role(context, "devops"),
    )

    task_record = TaskRecord(
        task_id=bundle.task_id,
        agent_type="devops",
        bundle=bundle.model_dump(mode="json"),
        status=TaskStatus.DISPATCHED.value,
        last_update=None,
        result=None,
        pr_url=None,
    )

    logger.info("Dispatched DevOps task %s", bundle.task_id)
    return {"tasks": [task_record]}


def handle_failure(state: OrchestratorState) -> dict[str, Any]:
    """
    Handle a failed task. Decide whether to retry, reassign, or escalate.

    For now, notifies the stakeholder. In the future, could:
    - Retry with adjusted parameters
    - Dispatch to a different agent
    - Create a JIRA ticket for manual intervention
    """
    tasks = state.get("tasks", [])
    failed_task = next(
        (t for t in reversed(tasks) if t["status"] == TaskStatus.FAILED.value),
        None,
    )

    if not failed_task:
        return {}

    result = failed_task.get("result", {})
    msg = (
        f"⚠ Task {failed_task['task_id']} ({failed_task['agent_type']}) failed.\n"
        f"Errors: {result.get('errors', ['Unknown']) if result else ['Unknown']}\n"
        "Escalating to stakeholder for guidance."
    )

    return {"messages": [{"role": "assistant", "content": msg}]}


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------


def build_orchestrator_graph() -> StateGraph:
    """
    Build the PA's StateGraph.

    The flow for each agent follows the same pattern:
        dispatch_X → wait_for_agent → evaluate_result → route

    The full happy path:
        plan_work → dispatch_coding → wait → evaluate
                  → dispatch_pr_review → wait → evaluate
                  → dispatch_uat → wait → evaluate
                  → dispatch_devops → wait → evaluate → END

    Each wait_for_agent is an INTERRUPT — the graph checkpoints and
    resumes only when the agent's callback arrives.
    """
    graph = StateGraph(OrchestratorState)

    # Nodes
    graph.add_node("plan_work", plan_work)
    graph.add_node("dispatch_coding", dispatch_coding)
    graph.add_node("dispatch_pr_review", dispatch_pr_review)
    graph.add_node("dispatch_uat", dispatch_uat)
    graph.add_node("dispatch_devops", dispatch_devops)
    graph.add_node("wait_for_agent", wait_for_agent)
    graph.add_node("evaluate_result", evaluate_result)
    graph.add_node("handle_failure", handle_failure)

    # Main flow
    graph.add_edge(START, "plan_work")
    graph.add_edge("plan_work", "dispatch_coding")

    # After any dispatch, wait for the agent
    graph.add_edge("dispatch_coding", "wait_for_agent")
    graph.add_edge("dispatch_pr_review", "wait_for_agent")
    graph.add_edge("dispatch_uat", "wait_for_agent")
    graph.add_edge("dispatch_devops", "wait_for_agent")

    # After agent reports back, evaluate
    graph.add_edge("wait_for_agent", "evaluate_result")

    # After evaluation, route to next step
    graph.add_conditional_edges("evaluate_result", route_after_evaluation)

    # Failure handling ends the flow (for now)
    graph.add_edge("handle_failure", END)

    return graph
