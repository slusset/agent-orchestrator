"""
Agent CLI Adapter: Pluggable interface for AI coding agent CLIs.

Wraps different coding agent CLIs (Claude Code, OpenAI Codex, Gemini CLI,
Aider, etc.) behind a uniform async interface. The CodingAgent uses
whichever CLI adapter is configured — it doesn't know or care which
LLM is doing the code generation.

Architecture:
    CodingAgent (outer loop: git, tests, PR, lifecycle)
        └── AgentCLI (inner loop: code generation)
                ├── ClaudeCodeCLI    → claude -p "task" --output-format json
                ├── CodexCLI         → codex exec "task" --json
                ├── GeminiCLI        → gemini -p "task" --output-format json
                ├── AiderCLI         → aider --message "task" files...
                └── (your CLI here)

Each adapter translates our uniform interface into the specific CLI's
flags and output format.

Traceability:
  Persona: specs/personas/coding-agent.md
  Journey: specs/journeys/agent-execution-lifecycle.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


@dataclass
class CLIRequest:
    """
    Uniform request to an agent CLI.

    This is what the CodingAgent passes to any CLI adapter.
    The adapter translates it into the specific CLI's arguments.
    """

    prompt: str
    work_dir: Path
    focus_files: list[str] = field(default_factory=list)
    read_only_files: list[str] = field(default_factory=list)
    context: str = ""  # Additional context to prepend to prompt
    max_turns: int | None = None  # Limit agentic iterations
    timeout: float = 600.0  # 10 minute default


@dataclass
class CLIResponse:
    """
    Uniform response from an agent CLI.

    All adapters normalize their output into this shape.
    """

    success: bool
    output: str  # Raw CLI output
    files_changed: list[str] = field(default_factory=list)
    exit_code: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class AgentCLI(ABC):
    """
    Abstract base for agent CLI adapters.

    Subclasses implement:
    - name: human-readable name
    - cli_command: the binary name (for availability check)
    - _build_args(): translate CLIRequest → CLI arguments
    - _parse_output(): translate CLI output → CLIResponse
    """

    name: str = "base"
    cli_command: str = "echo"

    def __init__(self, extra_args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        """
        Args:
            extra_args: Additional CLI arguments appended to every invocation
            env: Extra environment variables for the subprocess
        """
        self.extra_args = extra_args or []
        self.extra_env = env or {}

    @abstractmethod
    def _build_args(self, request: CLIRequest) -> list[str]:
        """
        Build the full CLI argument list from a request.

        Returns the complete argv list starting with the CLI binary.
        """

    def _parse_output(self, stdout: str, stderr: str, exit_code: int) -> CLIResponse:
        """
        Parse CLI output into a CLIResponse.

        Default implementation: success if exit_code == 0.
        Subclasses can override for structured output parsing.
        """
        return CLIResponse(
            success=exit_code == 0,
            output=stdout,
            exit_code=exit_code,
            error=stderr if exit_code != 0 else "",
        )

    async def run(self, request: CLIRequest) -> CLIResponse:
        """
        Execute the CLI with the given request.

        This is the main entry point — CodingAgent calls this.
        """
        args = self._build_args(request)
        cmd_str = " ".join(args[:3]) + "..."  # Truncated for logging
        logger.info("[%s] Running: %s (cwd=%s)", self.name, cmd_str, request.work_dir)

        env = {**os.environ, **self.extra_env}

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=request.work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=request.timeout
            )
        except asyncio.TimeoutError:
            logger.error("[%s] Command timed out after %.0fs", self.name, request.timeout)
            return CLIResponse(
                success=False,
                output="",
                exit_code=-1,
                error=f"CLI timed out after {request.timeout}s",
            )
        except FileNotFoundError:
            return CLIResponse(
                success=False,
                output="",
                exit_code=-1,
                error=f"CLI binary not found: {args[0]}. Is {self.name} installed?",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        response = self._parse_output(stdout, stderr, proc.returncode or 0)

        logger.info(
            "[%s] Finished: exit_code=%d, success=%s, output_len=%d",
            self.name, response.exit_code, response.success, len(response.output),
        )
        return response

    async def is_available(self) -> bool:
        """Check if the CLI binary is installed and accessible."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "which", self.cli_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    def _build_prompt(self, request: CLIRequest) -> str:
        """Build the full prompt including context."""
        parts = []
        if request.context:
            parts.append(request.context)
        parts.append(request.prompt)
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


class ClaudeCodeCLI(AgentCLI):
    """
    Claude Code CLI adapter.

    Invokes: claude -p "task" --output-format json --bare
    """

    name = "claude-code"
    cli_command = "claude"

    def __init__(
        self,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        allowed_tools: list[str] | None = None,
        max_turns: int | None = None,
    ) -> None:
        super().__init__(extra_args, env)
        self.allowed_tools = allowed_tools
        self.default_max_turns = max_turns

    def _build_args(self, request: CLIRequest) -> list[str]:
        prompt = self._build_prompt(request)

        args = [
            self.cli_command,
            "-p", prompt,
            "--output-format", "json",
        ]

        # Allowed tools for scope control
        if self.allowed_tools:
            for tool in self.allowed_tools:
                args.extend(["--allowedTools", tool])

        # Turn limit
        max_turns = request.max_turns or self.default_max_turns
        if max_turns:
            args.extend(["--max-turns", str(max_turns)])

        args.extend(self.extra_args)
        return args

    def _parse_output(self, stdout: str, stderr: str, exit_code: int) -> CLIResponse:
        """Parse Claude Code JSON output.

        Claude Code with --output-format json emits the same JSON to
        both stdout and stderr.  We try stdout first, then fall back
        to stderr if stdout is empty or not valid JSON.
        """
        raw = stdout or stderr  # Prefer stdout, fall back to stderr

        response = CLIResponse(
            success=exit_code == 0,
            output=raw,
            exit_code=exit_code,
            error="",
        )

        # Try to parse structured JSON output
        try:
            data = json.loads(raw)
            response.metadata = data

            if isinstance(data, dict):
                # Claude Code JSON has a "result" field with the final message
                response.output = data.get("result", raw)

                # Check for error signals in the JSON itself
                if data.get("is_error"):
                    response.success = False
                    response.error = data.get("result", "Unknown error")
                elif data.get("subtype") == "error_max_turns":
                    response.success = False
                    response.error = "Hit max turns limit"
                elif data.get("subtype", "").startswith("error"):
                    response.success = False
                    response.error = data.get("result", f"Error subtype: {data['subtype']}")

        except (json.JSONDecodeError, TypeError):
            # Non-JSON output — keep raw and use stderr for errors
            if exit_code != 0:
                response.error = stderr or stdout or "CLI failed with no output"

        return response


# ---------------------------------------------------------------------------
# OpenAI Codex
# ---------------------------------------------------------------------------


class CodexCLI(AgentCLI):
    """
    OpenAI Codex CLI adapter.

    Invokes: codex exec "task" --json --full-auto
    """

    name = "codex"
    cli_command = "codex"

    def __init__(
        self,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        sandbox: str = "workspace-write",
    ) -> None:
        super().__init__(extra_args, env)
        self.sandbox = sandbox  # read-only | workspace-write | danger-full-access

    def _build_args(self, request: CLIRequest) -> list[str]:
        prompt = self._build_prompt(request)

        args = [
            self.cli_command,
            "exec", prompt,
            "--json",
            "--full-auto",
            "--sandbox", self.sandbox,
        ]

        args.extend(self.extra_args)
        return args

    def _parse_output(self, stdout: str, stderr: str, exit_code: int) -> CLIResponse:
        """Parse Codex JSONL output."""
        response = CLIResponse(
            success=exit_code == 0,
            output=stdout,
            exit_code=exit_code,
            error=stderr if exit_code != 0 else "",
        )

        # Codex outputs newline-delimited JSON events
        events = []
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if events:
            response.metadata["events"] = events
            # Last event typically contains the final result
            last = events[-1]
            if isinstance(last, dict) and "message" in last:
                response.output = last["message"]

        return response


# ---------------------------------------------------------------------------
# Google Gemini CLI
# ---------------------------------------------------------------------------


class GeminiCLI(AgentCLI):
    """
    Google Gemini CLI adapter.

    Invokes: gemini -p "task" --non-interactive --output-format json
    """

    name = "gemini"
    cli_command = "gemini"

    def _build_args(self, request: CLIRequest) -> list[str]:
        prompt = self._build_prompt(request)

        args = [
            self.cli_command,
            "-p", prompt,
            "--non-interactive",
            "--output-format", "json",
        ]

        args.extend(self.extra_args)
        return args

    def _parse_output(self, stdout: str, stderr: str, exit_code: int) -> CLIResponse:
        """Parse Gemini CLI JSON output."""
        response = CLIResponse(
            success=exit_code == 0,
            output=stdout,
            exit_code=exit_code,
            error=stderr if exit_code != 0 else "",
        )

        try:
            data = json.loads(stdout)
            response.metadata = data
            if isinstance(data, dict):
                response.output = data.get("response", stdout)
                if data.get("error"):
                    response.error = data["error"]
                    response.success = False
        except (json.JSONDecodeError, TypeError):
            pass

        return response


# ---------------------------------------------------------------------------
# Aider
# ---------------------------------------------------------------------------


class AiderCLI(AgentCLI):
    """
    Aider CLI adapter.

    Invokes: aider --message "task" --yes --no-stream [files...]
    """

    name = "aider"
    cli_command = "aider"

    def __init__(
        self,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
        auto_commits: bool = False,
    ) -> None:
        super().__init__(extra_args, env)
        self.model = model
        self.auto_commits = auto_commits

    def _build_args(self, request: CLIRequest) -> list[str]:
        prompt = self._build_prompt(request)

        args = [
            self.cli_command,
            "--message", prompt,
            "--yes",  # Auto-confirm prompts
            "--no-stream",
        ]

        if self.model:
            args.extend(["--model", self.model])

        if not self.auto_commits:
            args.append("--no-auto-commits")

        # Add focus files as editable
        for f in request.focus_files:
            args.extend(["--file", f])

        # Add read-only reference files
        for f in request.read_only_files:
            args.extend(["--read", f])

        args.extend(self.extra_args)
        return args


# ---------------------------------------------------------------------------
# Generic / Custom CLI
# ---------------------------------------------------------------------------


class GenericCLI(AgentCLI):
    """
    Generic adapter for any CLI that accepts a prompt via arguments.

    Configure the command template at init time:

        cli = GenericCLI(
            name="my-tool",
            cli_command="my-tool",
            arg_template=["run", "--prompt", "{prompt}", "--quiet"],
        )

    The {prompt} placeholder is replaced with the actual prompt.
    """

    def __init__(
        self,
        name: str = "generic",
        cli_command: str = "echo",
        arg_template: list[str] | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(extra_args, env)
        self.name = name
        self.cli_command = cli_command
        self._template = arg_template or ["{prompt}"]

    def _build_args(self, request: CLIRequest) -> list[str]:
        prompt = self._build_prompt(request)

        args = [self.cli_command]
        for part in self._template:
            args.append(part.replace("{prompt}", prompt))

        args.extend(self.extra_args)
        return args


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Default CLI adapters — keyed by short name
CLI_REGISTRY: dict[str, type[AgentCLI]] = {
    "claude-code": ClaudeCodeCLI,
    "codex": CodexCLI,
    "gemini": GeminiCLI,
    "aider": AiderCLI,
    "generic": GenericCLI,
}


def create_cli(
    cli_type: str,
    **kwargs: Any,
) -> AgentCLI:
    """
    Factory: create a CLI adapter by type name.

    Usage:
        cli = create_cli("claude-code", max_turns=10)
        cli = create_cli("codex", sandbox="workspace-write")
        cli = create_cli("aider", model="claude-sonnet-4-20250514")
        cli = create_cli("generic", cli_command="hermes", arg_template=["--task", "{prompt}"])
    """
    cls = CLI_REGISTRY.get(cli_type)
    if not cls:
        raise ValueError(
            f"Unknown CLI type: {cli_type}. "
            f"Available: {list(CLI_REGISTRY.keys())}"
        )
    return cls(**kwargs)


def register_cli(name: str, cls: type[AgentCLI]) -> None:
    """Register a custom CLI adapter."""
    CLI_REGISTRY[name] = cls
