"""
Demo: End-to-end flow of the orchestrator.

Shows the full cycle:
1. Stakeholder sends a request
2. PA plans and dispatches to Coding Agent
3. Graph INTERRUPTS (waits for callback)
4. Coding Agent runs in background, completes
5. Callback resumes the graph
6. PA evaluates and routes to next agent

Run with: uv run python demo.py

Note: Requires ANTHROPIC_API_KEY for the planning step (LLM call).
Set it to skip planning: SKIP_LLM=1 uv run python demo.py
"""

import asyncio
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("demo")


async def main() -> None:
    from src.orchestrator.server import OrchestratorServer

    print("\n" + "=" * 60)
    print("  Agent Orchestrator — End-to-End Demo")
    print("=" * 60 + "\n")

    # Create the server (uses log transport for local dev)
    server = OrchestratorServer(transport="log")

    # Define the story
    story = {
        "story_id": "PROJ-42",
        "objective": "Add user authentication with email/password",
        "context": "We're building a Next.js app with Supabase backend. "
        "Need login, signup, and password reset flows.",
        "acceptance_criteria": [
            "Users can sign up with email and password",
            "Users can log in with existing credentials",
            "Users can reset their password via email",
            "All auth routes are protected",
            "Tests cover happy path and error cases",
        ],
        "repo_url": "git@github.com:example/my-app.git",
        "focus_paths": ["src/auth/", "src/app/(auth)/"],
        "protected_paths": [".github/", "infra/", "supabase/migrations/"],
    }

    # Step 1: Start the story
    print("📋 Step 1: Stakeholder sends request to PA\n")
    print(f"   Story: {story['story_id']} — {story['objective']}")
    print(f"   Criteria: {len(story['acceptance_criteria'])} acceptance criteria\n")

    try:
        thread_id = await server.start_story(
            message=f"Please implement {story['objective']}. "
            f"Story ID: {story['story_id']}. "
            f"Acceptance criteria: {', '.join(story['acceptance_criteria'])}",
            story=story,
            context={"callback_url": "http://localhost:8000/callback"},
        )
    except Exception as e:
        if "ANTHROPIC_API_KEY" in str(e) or "api_key" in str(e).lower():
            print("   ⚠ No ANTHROPIC_API_KEY set — running without LLM planning step")
            print("   (In production, the PA would use Claude to create the plan)\n")
            # Fall back to direct dispatch without LLM
            thread_id = await _demo_without_llm(server, story)
        else:
            raise

    print(f"\n   Thread ID: {thread_id}")
    print("   ⏸  Graph is now INTERRUPTED — waiting for agent callback\n")

    # Give the agent a moment to "work"
    await asyncio.sleep(0.5)

    # Step 2: Simulate the agent completing (in real life, this comes from the HTTP callback)
    print("🔧 Step 2: Coding Agent completes and sends callback\n")

    agent_result = {
        "task_id": _get_active_task_id(server, thread_id),
        "success": True,
        "summary": "Implemented user authentication with email/password. "
        "Added signup, login, and password reset flows with Supabase Auth.",
        "artifacts": ["https://github.com/example/my-app/pull/42"],
        "metadata": {
            "branch": "feature/abc123-add-user-authentication",
            "files_changed": [
                "src/auth/login.tsx",
                "src/auth/signup.tsx",
                "src/auth/reset-password.tsx",
                "src/auth/auth-provider.tsx",
                "src/auth/__tests__/auth.test.tsx",
            ],
            "tests_passed": True,
            "test_count": 12,
        },
    }

    print(f"   PR: {agent_result['artifacts'][0]}")
    print(f"   Files changed: {len(agent_result['metadata']['files_changed'])}")
    print(f"   Tests: {agent_result['metadata']['test_count']} passed\n")

    # Resume the graph
    result = await server.handle_agent_callback(thread_id, agent_result)

    print("\n📊 Step 3: PA evaluates and routes\n")
    if result:
        messages = result.get("messages", [])
        for msg in messages[-2:]:  # Show last couple messages
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            if content:
                print(f"   PA: {content}\n")

    print("=" * 60)
    print("  Demo complete! Graph interrupted again, waiting for PR Agent.")
    print("=" * 60 + "\n")

    await server.shutdown()


async def _demo_without_llm(server, story):
    """Run the demo without the LLM planning step by directly dispatching."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from src.contracts import CodingBundle, TaskStatus
    from src.orchestrator.state import OrchestratorState, TaskRecord

    thread_id = "demo-thread-001"
    config = {"configurable": {"thread_id": thread_id}}

    # Build bundle directly
    bundle = CodingBundle(
        objective=story["objective"],
        context=story["context"],
        acceptance_criteria=story["acceptance_criteria"],
        story_id=story["story_id"],
        callback_url="http://localhost:8000/callback",
        repo_url=story["repo_url"],
        focus_paths=story.get("focus_paths", []),
        protected_paths=story.get("protected_paths", []),
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

    # Manually set up state and invoke just the dispatch → wait portion
    # by starting the graph with a pre-planned state
    initial_state = {
        "messages": [
            {"role": "user", "content": f"Implement {story['objective']}"},
            {"role": "assistant", "content": "Plan: Dispatch to Coding Agent for implementation."},
        ],
        "tasks": [task_record],
        "current_story": story,
        "context": {"callback_url": "http://localhost:8000/callback"},
    }

    # We need a graph that starts at wait_for_agent
    # For the demo, we'll use the full graph but skip to dispatch
    from src.orchestrator.graph import build_orchestrator_graph

    graph = build_orchestrator_graph().compile(checkpointer=server.checkpointer)
    server.graph = graph

    # Store the task mapping
    server._task_to_thread[bundle.task_id] = thread_id

    # Invoke — will hit plan_work (needs LLM) so let's just set up state directly
    # and use update_state to jump ahead
    graph.update_state(config, initial_state)

    return thread_id


def _get_active_task_id(server, thread_id):
    """Get the task_id of the most recently dispatched task."""
    config = {"configurable": {"thread_id": thread_id}}
    state = server.graph.get_state(config)
    tasks = state.values.get("tasks", [])
    for task in reversed(tasks):
        if task["status"] == "dispatched":
            return task["task_id"]
    # Fallback
    return tasks[-1]["task_id"] if tasks else "unknown"


if __name__ == "__main__":
    asyncio.run(main())
