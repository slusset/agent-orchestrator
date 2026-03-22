#!/usr/bin/env python3
"""
Demo: Hello World Agent Orchestration

Runs the full dispatch→execute→callback→resume loop locally:

  1. Starts the PA callback server (FastAPI on port 9000)
  2. Starts a stub agent runner (FastAPI on port 9001)
  3. PA dispatches a TaskBundle to the stub agent via HTTP
  4. Stub agent sends heartbeats back to the PA callback server
  5. Stub agent sends TaskResult, PA logs the completion

Run with:
    uv run python demo.py

What to watch for in the logs:
    [callback]  — PA receiving agent heartbeats and results
    [stub]      — stub agent executing its work cycle
    [dispatch]  — PA dispatching the TaskBundle
    [runner]    — agent runner accepting and launching the task

Pass --fail to see the failure path:
    uv run python demo.py --fail
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import httpx
import uvicorn

# ---------------------------------------------------------------------------
# Logging setup — this is the "runtime observability" capability
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
# Quiet down noisy libraries
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("demo")


# ---------------------------------------------------------------------------
# Server startup helpers
# ---------------------------------------------------------------------------


async def start_callback_server(port: int = 9000):
    """Start the PA callback server in the background."""
    from src.orchestrator.callback_server import create_callback_app

    app, callback_server = create_callback_app(callback_path="/callback")

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    task = asyncio.create_task(server.serve())
    # Wait for server to be ready
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"http://127.0.0.1:{port}/health")
                if r.status_code == 200:
                    break
        except Exception:
            await asyncio.sleep(0.1)

    return server, task, callback_server


async def start_agent_runner(port: int = 9001):
    """Start the stub agent runner in the background."""
    from src.agents.runner import create_agent_app

    app = create_agent_app("stub", max_concurrent=2, transport="http")

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    task = asyncio.create_task(server.serve())
    # Wait for server to be ready
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"http://127.0.0.1:{port}/status")
                if r.status_code == 200:
                    break
        except Exception:
            await asyncio.sleep(0.1)

    return server, task


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


async def run_demo(should_fail: bool = False):
    """Run the full orchestration demo."""
    PA_PORT = 9000
    AGENT_PORT = 9001
    CALLBACK_URL = f"http://127.0.0.1:{PA_PORT}/callback"

    logger.info("=" * 60)
    logger.info("  AGENT ORCHESTRATOR — Hello World Demo")
    logger.info("=" * 60)
    logger.info("")

    # --- Step 1: Start servers ---
    logger.info("Starting PA callback server on port %d...", PA_PORT)
    pa_server, pa_task, callback_srv = await start_callback_server(PA_PORT)

    logger.info("Starting stub agent runner on port %d...", AGENT_PORT)
    agent_server, agent_task = await start_agent_runner(AGENT_PORT)

    logger.info("Both servers running")
    logger.info("")

    try:
        # --- Step 2: Check agent status ---
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{AGENT_PORT}/status")
            status = r.json()
            logger.info(
                "Agent runner status: type=%s, available=%s, max_concurrent=%d",
                status["agent_type"],
                status["available"],
                status["max_concurrent"],
            )

        # --- Step 3: Dispatch TaskBundle ---
        logger.info("")
        logger.info("Dispatching TaskBundle to stub agent...")
        bundle = {
            "task_id": "demo-001",
            "objective": "Say hello to the world",
            "callback_url": CALLBACK_URL,
            "acceptance_criteria": [
                "Agent receives the bundle",
                "Agent sends heartbeats",
                "Agent returns success",
            ],
            "metadata": {
                "work_seconds": 2.0,
                "fail": should_fail,
                "fail_message": "Demo intentional failure — this is expected!",
            },
        }

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"http://127.0.0.1:{AGENT_PORT}/execute",
                json={"agent_type": "stub", "bundle": bundle},
                timeout=5.0,
            )
            result = r.json()
            logger.info(
                "Agent accepted task: id=%s, status=%s",
                result["task_id"],
                result["status"],
            )

        # --- Step 4: Wait for agent to finish ---
        logger.info("")
        logger.info("Waiting for agent to complete (watching callbacks)...")
        logger.info("")

        start = time.monotonic()
        timeout = 10.0

        while time.monotonic() - start < timeout:
            await asyncio.sleep(0.3)

            # Check if we got a result callback
            if callback_srv.received_results:
                break

            # Log any heartbeats we've received
            if callback_srv.received_updates:
                for update in callback_srv.received_updates:
                    logger.info(
                        "  Heartbeat: status=%s, progress=%s%%, message=%s",
                        update.get("status", "?"),
                        update.get("progress_pct", "?"),
                        update.get("message", ""),
                    )
                callback_srv._received_updates.clear()

        # --- Step 5: Report results ---
        logger.info("")
        if callback_srv.received_results:
            result = callback_srv.received_results[-1]
            success = result.get("success", False)
            if success:
                logger.info("=" * 60)
                logger.info("  DEMO PASSED — Full loop completed successfully!")
                logger.info("=" * 60)
                logger.info("  Task ID:    %s", result.get("task_id"))
                logger.info("  Summary:    %s", result.get("summary"))
                logger.info("  Artifacts:  %s", result.get("artifacts", []))
            else:
                logger.info("=" * 60)
                logger.info("  DEMO: Agent reported failure (expected if --fail)")
                logger.info("=" * 60)
                logger.info("  Task ID:    %s", result.get("task_id"))
                logger.info("  Summary:    %s", result.get("summary"))
                logger.info("  Errors:     %s", result.get("errors", []))
        else:
            logger.error("  TIMEOUT — no result received within %.0fs", timeout)

        # --- Step 6: Health check ---
        logger.info("")
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{PA_PORT}/health")
            health = r.json()
            logger.info("PA health: %s", health)

    finally:
        # --- Shutdown ---
        logger.info("")
        logger.info("Shutting down servers...")
        pa_server.should_exit = True
        agent_server.should_exit = True
        await asyncio.gather(pa_task, agent_task, return_exceptions=True)
        logger.info("Done.")


def main():
    should_fail = "--fail" in sys.argv
    asyncio.run(run_demo(should_fail))


if __name__ == "__main__":
    main()
