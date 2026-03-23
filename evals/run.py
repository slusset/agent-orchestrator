#!/usr/bin/env python3
"""
Run the eval suite.

Usage:
    # Run all tasks with Claude Code (default)
    uv run python -m evals.run

    # Run with specific CLIs
    uv run python -m evals.run --cli claude-code codex

    # Run a single task
    uv run python -m evals.run --task eval-hello-endpoint

    # Run with verbose output
    uv run python -m evals.run -v

    # Save results to file
    uv run python -m evals.run --output evals/results/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from evals.task import discover_tasks
from evals.runner import EvalRunner, save_results


LOG_FORMAT = (
    "\033[90m%(asctime)s.%(msecs)03d\033[0m "
    "%(levelname)-5s "
    "\033[1m%(message)s\033[0m"
)


def main():
    parser = argparse.ArgumentParser(description="Run coding agent evals")
    parser.add_argument(
        "--cli", nargs="+", default=["claude-code"],
        help="CLI adapter(s) to test (default: claude-code)",
    )
    parser.add_argument(
        "--task", type=str, default=None,
        help="Run only a specific task by ID",
    )
    parser.add_argument(
        "--tasks-dir", type=str, default=str(project_root / "evals" / "tasks"),
        help="Directory containing eval tasks",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Directory to save results JSON",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover and validate tasks without running them",
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger("eval")

    # Discover tasks
    tasks_dir = Path(args.tasks_dir)
    tasks = discover_tasks(tasks_dir)

    if not tasks:
        logger.error("No valid tasks found in %s", tasks_dir)
        sys.exit(1)

    # Filter to specific task if requested
    if args.task:
        tasks = [t for t in tasks if t.task_id == args.task]
        if not tasks:
            logger.error("Task not found: %s", args.task)
            sys.exit(1)

    # Report discovered tasks
    logger.info("=" * 60)
    logger.info("  AGENT EVAL SUITE")
    logger.info("=" * 60)
    logger.info("Tasks: %d", len(tasks))
    logger.info("CLIs:  %s", ", ".join(args.cli))
    logger.info("Total runs: %d", len(tasks) * len(args.cli))
    logger.info("")

    for task in tasks:
        logger.info("  [%s] %s (%s)", task.difficulty, task.name, task.task_id)

    logger.info("")

    if args.dry_run:
        logger.info("Dry run — validating tasks only")
        for task in tasks:
            issues = task.validate()
            if issues:
                logger.warning("  %s: %s", task.task_id, issues)
            else:
                logger.info("  %s: ✓ valid", task.task_id)
        return

    # Run evals
    runner = EvalRunner(cli_types=args.cli)
    results = asyncio.run(runner.run_all(tasks))

    # Print results
    logger.info("")
    logger.info("=" * 60)
    logger.info("  RESULTS")
    logger.info("=" * 60)
    print(results.summary())

    # Save results if output dir specified
    if args.output:
        output_path = save_results(results, Path(args.output))
        logger.info("Results saved to %s", output_path)

    # Exit code based on pass rate
    sys.exit(0 if results.failed == 0 else 1)


if __name__ == "__main__":
    main()
