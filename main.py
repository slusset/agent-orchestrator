"""
Agent Orchestrator — Entry point.

The Primary Agent (PM) orchestrates specialized agents:
- Coding Agent: implements features, creates PRs
- UAT Agent: validates against specs and user journeys
- DevOps Agent: deploys across environments
- PR Agent: shepherds PRs through review

Communication is contract-based via TaskBundles with
transport-agnostic status reporting.
"""

import logging

from src.orchestrator import build_orchestrator_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    graph = build_orchestrator_graph()
    compiled = graph.compile()
    logger.info("Orchestrator graph compiled: %s", list(compiled.get_graph().nodes))


if __name__ == "__main__":
    main()
