#!/usr/bin/env python3
"""
Demo: End-to-end credential passthrough through the LangGraph orchestrator.

Boots the PA with SOPS → DotEnv → Env credential chain, runs the full
graph (plan_work → dispatch_coding → wait_for_agent → evaluate → END),
and verifies that the coding agent receives resolved credentials in its
CLI subprocess environment.

Run with:
    SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt uv run python demo_credentials.py

What it proves:
    1. Server boots and resolves credentials from SOPS-encrypted file
    2. Credentials flow through LangGraph graph context
    3. dispatch_coding creates CodingBundle with per-role resolved_env
    4. Credentials survive TaskRecord serialization (re-injection)
    5. Agent receives credentials in CLI.extra_env

Requirements:
    - ANTHROPIC_API_KEY must be resolvable (via .pm/secrets.env, .env, or os.environ)
    - sops + age installed (brew install sops age)
    - SOPS_AGE_KEY_FILE set to your age key path
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="\033[90m%(asctime)s.%(msecs)03d\033[0m %(levelname)-5s \033[1m%(message)s\033[0m",
    datefmt="%H:%M:%S",
)
# Quiet noisy libraries
for lib in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("demo-creds")


async def run_demo():
    from src.contracts.credentials import (
        ChainCredentialProvider,
        DotEnvCredentialProvider,
        EnvCredentialProvider,
        SopsCredentialProvider,
    )
    from src.contracts.capability_profile import (
        AgentCapabilityProfile,
        InvocationMethod,
    )
    from src.orchestrator.server import OrchestratorServer

    logger.info("=" * 60)
    logger.info("  CREDENTIAL PASSTHROUGH — End-to-End Demo")
    logger.info("=" * 60)
    logger.info("")

    # ------------------------------------------------------------------
    # Step 1: Build the credential provider chain
    # ------------------------------------------------------------------
    logger.info("Step 1: Building credential provider chain...")

    providers = []
    sops_path = Path(".pm/secrets.env")
    dotenv_path = Path(".env")

    if sops_path.exists():
        logger.info("  ✓ SOPS encrypted file found: %s", sops_path)
        providers.append(SopsCredentialProvider(sops_path))
    else:
        logger.info("  · No SOPS file at %s — skipping", sops_path)

    if dotenv_path.exists():
        logger.info("  ✓ .env file found: %s", dotenv_path)
        providers.append(DotEnvCredentialProvider(dotenv_path))
    else:
        logger.info("  · No .env file — skipping")

    providers.append(EnvCredentialProvider())
    logger.info("  ✓ Environment variables (always available)")

    chain = ChainCredentialProvider(providers)
    logger.info("  → Chain: %s", " → ".join(p.name for p in providers))
    logger.info("")

    # ------------------------------------------------------------------
    # Step 2: Boot the server
    # ------------------------------------------------------------------
    logger.info("Step 2: Booting OrchestratorServer...")

    server = OrchestratorServer(
        transport="log",
        credential_provider=chain,
        manifest_path=".pm/credentials.yaml",
    )
    result = await server.boot()

    if result is None:
        logger.error("  ✗ No credential manifest found at .pm/credentials.yaml")
        sys.exit(1)

    logger.info("  Resolution: %s", result.summary())

    if not result.ok:
        logger.warning("  ⚠ Some credentials missing — continuing anyway")

    # Show per-role breakdown
    for role in ("coding", "pr", "uat", "devops"):
        env = server.get_resolved_env(role)
        keys = list(env.keys())
        logger.info("  %s role → %d credentials: %s", role, len(keys), keys)

    logger.info("")

    # ------------------------------------------------------------------
    # Step 3: Run the LangGraph
    # ------------------------------------------------------------------
    logger.info("Step 3: Running LangGraph (plan → dispatch → interrupt)...")
    logger.info("  This calls Claude via ChatAnthropic for planning.")
    logger.info("")

    # We need to intercept the dispatch to inspect the bundle,
    # since the CodingAgent would try to clone a real repo.
    # Strategy: capture what the dispatcher receives.
    captured_bundles = []
    original_dispatch = server.dispatcher.dispatch

    async def capturing_dispatch(task_record):
        """Capture the task record (including re-injected credentials)."""
        captured_bundles.append(dict(task_record))
        logger.info("  [dispatch] Captured task %s for agent '%s'",
                     task_record["task_id"], task_record["agent_type"])

        resolved = task_record.get("_resolved_env", {})
        if resolved:
            # Mask values for logging
            masked = {k: f"{v[:8]}..." if len(v) > 8 else "***" for k, v in resolved.items()}
            logger.info("  [dispatch] resolved_env keys: %s", masked)
        else:
            logger.warning("  [dispatch] ⚠ No _resolved_env on task record!")

        # Don't actually dispatch — we just wanted to inspect
        logger.info("  [dispatch] (Skipping actual agent launch for demo)")

    server.dispatcher.dispatch = capturing_dispatch

    try:
        thread_id = await server.start_story(
            message="Implement a /health endpoint that returns JSON with uptime and version",
            story={
                "objective": "Add a /health endpoint returning JSON with uptime and version",
                "repo_url": "git@github.com:example/demo-app.git",
                "acceptance_criteria": [
                    "GET /health returns 200 with JSON body",
                    "Response includes 'uptime_seconds' and 'version' fields",
                    "Endpoint has a unit test",
                ],
                "context": "FastAPI application, Python 3.12, pytest for tests",
            },
            context={
                "callback_url": "http://localhost:9000/callback",
            },
        )
        logger.info("  Graph completed initial run on thread %s", thread_id)
    except Exception as e:
        logger.error("  Graph execution error: %s", e)
        logger.error("  (This is expected if ANTHROPIC_API_KEY is not set in the PA's environment)")
        sys.exit(1)

    logger.info("")

    # ------------------------------------------------------------------
    # Step 4: Verify credentials in the captured dispatch
    # ------------------------------------------------------------------
    logger.info("Step 4: Verifying credential passthrough...")

    if not captured_bundles:
        logger.error("  ✗ No bundles captured — graph may not have reached dispatch_coding")
        sys.exit(1)

    task = captured_bundles[0]
    resolved = task.get("_resolved_env", {})

    checks = []
    if "ANTHROPIC_API_KEY" in resolved:
        checks.append(("ANTHROPIC_API_KEY", True, f"{resolved['ANTHROPIC_API_KEY'][:12]}..."))
    else:
        checks.append(("ANTHROPIC_API_KEY", False, "MISSING"))

    if "GITHUB_TOKEN" in resolved:
        checks.append(("GITHUB_TOKEN", True, f"{resolved['GITHUB_TOKEN'][:12]}..."))
    else:
        checks.append(("GITHUB_TOKEN", False, "MISSING (ok if not configured)"))

    all_passed = True
    for name, present, display in checks:
        status = "✓" if present else "·"
        logger.info("  %s %s = %s", status, name, display)
        if name == "ANTHROPIC_API_KEY" and not present:
            all_passed = False

    logger.info("")

    # ------------------------------------------------------------------
    # Step 5: Simulate the agent receiving the bundle
    # ------------------------------------------------------------------
    logger.info("Step 5: Simulating CodingAgent receiving the bundle...")

    from src.contracts.coding_bundle import CodingBundle
    from src.agents.coding_agent import CodingAgent

    bundle_data = task["bundle"]
    bundle = CodingBundle.model_validate(bundle_data)

    # Show that resolved_env is empty after deserialization
    logger.info("  Bundle.resolved_env after deserialization: %s",
                bundle.resolved_env or "(empty — stripped by exclude=True)")

    # Re-inject as the dispatcher would
    bundle.resolved_env = resolved
    logger.info("  Bundle.resolved_env after re-injection: %d keys", len(bundle.resolved_env))

    # Create agent — this merges resolved_env into CLI.extra_env
    agent = CodingAgent(bundle=bundle)
    cli_env = agent.cli.extra_env

    logger.info("  CLI.extra_env keys: %s", list(cli_env.keys()))
    if "ANTHROPIC_API_KEY" in cli_env:
        logger.info("  ✓ CodingAgent CLI has ANTHROPIC_API_KEY = %s...", cli_env["ANTHROPIC_API_KEY"][:12])
    else:
        logger.error("  ✗ CodingAgent CLI is MISSING ANTHROPIC_API_KEY!")
        all_passed = False

    logger.info("")

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------
    if all_passed:
        logger.info("=" * 60)
        logger.info("  ✅ CREDENTIAL PASSTHROUGH VERIFIED")
        logger.info("=" * 60)
        logger.info("")
        logger.info("  SOPS → Provider Chain → Server.boot() → Graph Context")
        logger.info("  → dispatch_coding → TaskRecord → Server re-injection")
        logger.info("  → Dispatcher → CodingBundle.resolved_env → CLI.extra_env")
        logger.info("")
        logger.info("  The agent subprocess would receive credentials via env vars.")
    else:
        logger.error("=" * 60)
        logger.error("  ❌ CREDENTIAL PASSTHROUGH FAILED")
        logger.error("=" * 60)
        sys.exit(1)


def main():
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
