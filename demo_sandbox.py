#!/usr/bin/env python3
"""
Demo: Real CodingAgent run against a live GitHub repo.

Boots the PA with credential resolution, dispatches a CodingAgent
against slusset/agent-sandbox, and watches it:
  1. Clone the repo
  2. Create a feature branch
  3. Invoke Claude Code CLI to implement changes
  4. Run pytest
  5. Push branch and create a draft PR

Run with:
    uv run python demo_sandbox.py

Prerequisites:
    - ANTHROPIC_API_KEY resolvable (SOPS, .env, or os.environ)
    - GITHUB_TOKEN resolvable (fine-grained PAT with Contents:Write + PRs:RW)
    - `claude` CLI installed (Claude Code)
    - `gh` CLI installed
    - SOPS_AGE_KEY_FILE set (if using SOPS)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = (
    "\033[90m%(asctime)s.%(msecs)03d\033[0m "
    "%(levelname)-5s "
    "\033[1m%(message)s\033[0m"
)

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%H:%M:%S",
)
# Show agent internals
logging.getLogger("src.agents").setLevel(logging.DEBUG)
logging.getLogger("src.orchestrator").setLevel(logging.DEBUG)
# Quiet noisy libraries
for lib in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("demo-sandbox")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SANDBOX_REPO = "https://github.com/slusset/agent-sandbox.git"
BASE_BRANCH = "main"
OBJECTIVE = (
    "Create a Python module src/health.py that provides a health_status() function. "
    "It should return a dict with keys: 'status' (always 'ok'), 'version' (read from "
    "a VERSION variable set to '0.1.0'), and 'python_version' (from sys.version_info, "
    "as a string like '3.12.1'). "
    "Also create tests/test_health.py with pytest tests covering all three fields."
)
ACCEPTANCE_CRITERIA = [
    "src/health.py exists with health_status() function",
    "health_status() returns dict with 'status', 'version', 'python_version' keys",
    "tests/test_health.py exists with at least 3 tests",
    "All tests pass with pytest",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_demo():
    from src.contracts.credentials import (
        ChainCredentialProvider,
        DotEnvCredentialProvider,
        EnvCredentialProvider,
        SopsCredentialProvider,
    )
    from src.contracts.coding_bundle import CodingBundle
    from src.contracts.status_reporter import LogStatusReporter
    from src.agents.coding_agent import CodingAgent

    logger.info("=" * 60)
    logger.info("  SANDBOX RUN — Real CodingAgent against GitHub")
    logger.info("=" * 60)
    logger.info("")

    # ------------------------------------------------------------------
    # Step 1: Resolve credentials
    # ------------------------------------------------------------------
    logger.info("Step 1: Resolving credentials...")

    providers = []
    sops_path = Path(".pm/secrets.env")
    dotenv_path = Path(".env")

    if sops_path.exists():
        providers.append(SopsCredentialProvider(sops_path))
    if dotenv_path.exists():
        providers.append(DotEnvCredentialProvider(dotenv_path))
    providers.append(EnvCredentialProvider())

    chain = ChainCredentialProvider(providers)

    from src.contracts.credentials import load_credential_manifest
    manifest = load_credential_manifest(".pm/credentials.yaml")
    result = await chain.resolve_for_role(manifest, "coding")

    logger.info("  Resolution: %s", result.summary())
    coding_env = result.as_env()

    # Verify required credentials
    if "ANTHROPIC_API_KEY" not in coding_env:
        logger.error("  ANTHROPIC_API_KEY not resolved — cannot invoke Claude Code")
        sys.exit(1)
    if "GITHUB_TOKEN" not in coding_env:
        logger.error("  GITHUB_TOKEN not resolved — cannot push or create PR")
        sys.exit(1)

    logger.info("  ANTHROPIC_API_KEY = %s...", coding_env["ANTHROPIC_API_KEY"][:12])
    logger.info("  GITHUB_TOKEN = %s...", coding_env["GITHUB_TOKEN"][:12])
    logger.info("")

    # ------------------------------------------------------------------
    # Step 2: Inject GITHUB_TOKEN into PA process env
    # ------------------------------------------------------------------
    # GitWorkspace._run_git() and _run_command() use os.environ directly,
    # not resolved_env. So git push and gh pr create need the token in
    # the process environment. (This is a known gap to fix later —
    # workspace should accept an env override.)
    logger.info("Step 2: Injecting GITHUB_TOKEN into process environment...")
    os.environ["GITHUB_TOKEN"] = coding_env["GITHUB_TOKEN"]
    logger.info("  Done (git push and gh pr create will use this)")
    logger.info("")

    # ------------------------------------------------------------------
    # Step 3: Load agent rules and build CodingBundle
    # ------------------------------------------------------------------
    logger.info("Step 3: Building CodingBundle...")

    # Load agent rules (same as OrchestratorServer.boot() does)
    agent_rules = ""
    rules_path = Path(".pm/agent-rules.md")
    if rules_path.exists():
        agent_rules = rules_path.read_text()
        logger.info("  Loaded agent rules from %s", rules_path)

    bundle_context = OBJECTIVE
    if agent_rules:
        bundle_context = f"{agent_rules}"

    bundle = CodingBundle(
        objective=OBJECTIVE,
        acceptance_criteria=ACCEPTANCE_CRITERIA,
        context=bundle_context,
        callback_url="http://localhost:9000/callback",  # Not used with LogStatusReporter
        repo_url=SANDBOX_REPO,
        base_branch=BASE_BRANCH,
        branch_prefix="feature/",
        focus_paths=["src/", "tests/"],
        run_unit_tests=True,
        run_integration_tests=False,
        test_frameworks=["pytest"],
        draft_pr=True,
        timeout_minutes=10,
        cli_type="claude-code",
        resolved_env=coding_env,
    )

    logger.info("  Task ID:    %s", bundle.task_id)
    logger.info("  Objective:  %s", bundle.objective[:80] + "...")
    logger.info("  Repo:       %s", bundle.repo_url)
    logger.info("  CLI:        %s", bundle.cli_type)
    logger.info("  Tests:      pytest (run_unit_tests=%s)", bundle.run_unit_tests)
    logger.info("  Timeout:    %d minutes", bundle.timeout_minutes)
    logger.info("")

    # ------------------------------------------------------------------
    # Step 4: Run CodingAgent
    # ------------------------------------------------------------------
    logger.info("Step 4: Running CodingAgent...")
    logger.info("  This will: clone → branch → Claude Code → test → push → PR")
    logger.info("  Watch for [coding] and [git] log prefixes below.")
    logger.info("")
    logger.info("-" * 60)

    reporter = LogStatusReporter(bundle)
    agent = CodingAgent(bundle=bundle, reporter=reporter)

    try:
        await agent.run()
    except Exception as e:
        logger.error("")
        logger.error("=" * 60)
        logger.error("  AGENT FAILED: %s", e)
        logger.error("=" * 60)
        logger.exception("Full traceback:")
        sys.exit(1)

    logger.info("-" * 60)
    logger.info("")

    # ------------------------------------------------------------------
    # Step 5: Report results
    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("  SANDBOX RUN COMPLETE")
    logger.info("=" * 60)
    logger.info("")
    logger.info("  Check your GitHub repo for the draft PR:")
    logger.info("  https://github.com/slusset/agent-sandbox/pulls")
    logger.info("")


def main():
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
