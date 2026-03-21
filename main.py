"""
Agent Orchestrator — Entry point.

The Primary Agent (PM) orchestrates specialized agents:
- Coding Agent: implements features, creates PRs
- UAT Agent: validates against specs and user journeys
- DevOps Agent: deploys across environments
- PR Agent: shepherds PRs through review

Communication is contract-based via TaskBundles with
transport-agnostic status reporting. Agents run as separate
processes; the graph interrupts and resumes on callbacks.

Usage:
    uv run python main.py          # Verify graph compiles
    uv run python demo.py          # Run end-to-end demo
"""

import logging

from src.orchestrator import build_orchestrator_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    graph = build_orchestrator_graph()
    compiled = graph.compile(checkpointer=MemorySaver())
    nodes = list(compiled.get_graph().nodes)
    logger.info("Orchestrator graph compiled with %d nodes: %s", len(nodes), nodes)


if __name__ == "__main__":
    main()
