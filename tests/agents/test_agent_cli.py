"""
Unit tests for the Agent CLI adapter — pluggable interface for coding agent CLIs.

Tests cover argument building, output parsing, the registry, and
integration with CodingAgent. Does NOT invoke real CLI binaries
(those are integration tests).

Traceability:
  Persona: specs/personas/coding-agent.md
  Journey: specs/journeys/agent-execution-lifecycle.md
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.agent_cli import (
    AgentCLI,
    AiderCLI,
    CLIRequest,
    CLIResponse,
    CLI_REGISTRY,
    ClaudeCodeCLI,
    CodexCLI,
    GeminiCLI,
    GenericCLI,
    create_cli,
    register_cli,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_request(tmp_path):
    return CLIRequest(
        prompt="Add a hello world endpoint",
        work_dir=tmp_path,
        focus_files=["src/app.py"],
        read_only_files=["README.md"],
        context="This is a FastAPI project",
        timeout=60.0,
    )


# ---------------------------------------------------------------------------
# CLIRequest / CLIResponse
# ---------------------------------------------------------------------------


class TestCLIRequest:

    def test_defaults(self, tmp_path):
        req = CLIRequest(prompt="test", work_dir=tmp_path)
        assert req.focus_files == []
        assert req.read_only_files == []
        assert req.context == ""
        assert req.max_turns is None
        assert req.timeout == 600.0

    def test_all_fields(self, basic_request):
        assert basic_request.prompt == "Add a hello world endpoint"
        assert basic_request.context == "This is a FastAPI project"
        assert len(basic_request.focus_files) == 1


class TestCLIResponse:

    def test_success(self):
        r = CLIResponse(success=True, output="Done", exit_code=0)
        assert r.success
        assert r.files_changed == []
        assert r.error == ""

    def test_failure(self):
        r = CLIResponse(success=False, output="", exit_code=1, error="crash")
        assert not r.success
        assert r.error == "crash"


# ---------------------------------------------------------------------------
# Claude Code CLI
# ---------------------------------------------------------------------------


class TestClaudeCodeCLI:

    def test_build_args_basic(self, basic_request):
        cli = ClaudeCodeCLI()
        args = cli._build_args(basic_request)

        assert args[0] == "claude"
        assert "-p" in args
        # Prompt should include context
        prompt_idx = args.index("-p") + 1
        assert "FastAPI project" in args[prompt_idx]
        assert "hello world endpoint" in args[prompt_idx]
        assert "--output-format" in args
        assert "json" in args

    def test_build_args_with_tools(self, basic_request):
        cli = ClaudeCodeCLI(allowed_tools=["Read", "Edit", "Bash"])
        args = cli._build_args(basic_request)

        assert args.count("--allowedTools") == 3

    def test_build_args_with_max_turns(self, basic_request):
        cli = ClaudeCodeCLI(max_turns=5)
        args = cli._build_args(basic_request)

        idx = args.index("--max-turns")
        assert args[idx + 1] == "5"

    def test_build_args_request_max_turns_overrides(self, basic_request):
        cli = ClaudeCodeCLI(max_turns=10)
        basic_request.max_turns = 3
        args = cli._build_args(basic_request)

        idx = args.index("--max-turns")
        assert args[idx + 1] == "3"

    def test_build_args_extra_args(self, basic_request):
        cli = ClaudeCodeCLI(extra_args=["--bare", "--no-session-persistence"])
        args = cli._build_args(basic_request)

        assert "--bare" in args
        assert "--no-session-persistence" in args

    def test_parse_json_output(self):
        cli = ClaudeCodeCLI()
        data = json.dumps({"result": "Created the endpoint", "session_id": "abc"})
        response = cli._parse_output(data, "", 0)

        assert response.success
        assert response.output == "Created the endpoint"
        assert response.metadata.get("session_id") == "abc"

    def test_parse_non_json_output(self):
        cli = ClaudeCodeCLI()
        response = cli._parse_output("plain text output", "", 0)

        assert response.success
        assert response.output == "plain text output"

    def test_parse_failure(self):
        cli = ClaudeCodeCLI()
        response = cli._parse_output("", "API error", 1)

        assert not response.success
        assert response.error == "API error"

    def test_name_and_command(self):
        cli = ClaudeCodeCLI()
        assert cli.name == "claude-code"
        assert cli.cli_command == "claude"


# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------


class TestCodexCLI:

    def test_build_args_basic(self, basic_request):
        cli = CodexCLI()
        args = cli._build_args(basic_request)

        assert args[0] == "codex"
        assert args[1] == "exec"
        assert "--json" in args
        assert "--full-auto" in args
        assert "--sandbox" in args
        assert "workspace-write" in args

    def test_build_args_custom_sandbox(self, basic_request):
        cli = CodexCLI(sandbox="danger-full-access")
        args = cli._build_args(basic_request)

        idx = args.index("--sandbox")
        assert args[idx + 1] == "danger-full-access"

    def test_parse_jsonl_output(self):
        cli = CodexCLI()
        stdout = (
            '{"type": "start", "task": "working"}\n'
            '{"type": "done", "message": "Created endpoint"}\n'
        )
        response = cli._parse_output(stdout, "", 0)

        assert response.success
        assert len(response.metadata["events"]) == 2
        assert response.output == "Created endpoint"

    def test_name_and_command(self):
        cli = CodexCLI()
        assert cli.name == "codex"
        assert cli.cli_command == "codex"


# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------


class TestGeminiCLI:

    def test_build_args_basic(self, basic_request):
        cli = GeminiCLI()
        args = cli._build_args(basic_request)

        assert args[0] == "gemini"
        assert "-p" in args
        assert "--non-interactive" in args
        assert "--output-format" in args

    def test_parse_json_output(self):
        cli = GeminiCLI()
        data = json.dumps({"response": "Done", "stats": {"tokens": 100}})
        response = cli._parse_output(data, "", 0)

        assert response.success
        assert response.output == "Done"

    def test_parse_error_in_json(self):
        cli = GeminiCLI()
        data = json.dumps({"response": "", "error": "Rate limited"})
        response = cli._parse_output(data, "", 0)

        assert not response.success
        assert response.error == "Rate limited"

    def test_name_and_command(self):
        cli = GeminiCLI()
        assert cli.name == "gemini"
        assert cli.cli_command == "gemini"


# ---------------------------------------------------------------------------
# Aider CLI
# ---------------------------------------------------------------------------


class TestAiderCLI:

    def test_build_args_basic(self, basic_request):
        cli = AiderCLI()
        args = cli._build_args(basic_request)

        assert args[0] == "aider"
        assert "--message" in args
        assert "--yes" in args
        assert "--no-stream" in args
        assert "--no-auto-commits" in args

    def test_build_args_with_model(self, basic_request):
        cli = AiderCLI(model="claude-sonnet-4-20250514")
        args = cli._build_args(basic_request)

        idx = args.index("--model")
        assert args[idx + 1] == "claude-sonnet-4-20250514"

    def test_build_args_with_files(self, basic_request):
        cli = AiderCLI()
        args = cli._build_args(basic_request)

        # Focus files become --file args
        assert "--file" in args
        idx = args.index("--file")
        assert args[idx + 1] == "src/app.py"

        # Read-only files become --read args
        assert "--read" in args
        idx = args.index("--read")
        assert args[idx + 1] == "README.md"

    def test_auto_commits_flag(self, basic_request):
        cli = AiderCLI(auto_commits=True)
        args = cli._build_args(basic_request)
        assert "--no-auto-commits" not in args

    def test_name_and_command(self):
        cli = AiderCLI()
        assert cli.name == "aider"
        assert cli.cli_command == "aider"


# ---------------------------------------------------------------------------
# Generic CLI
# ---------------------------------------------------------------------------


class TestGenericCLI:

    def test_basic_template(self, basic_request):
        cli = GenericCLI(
            name="hermes",
            cli_command="hermes",
            arg_template=["--task", "{prompt}", "--quiet"],
        )
        args = cli._build_args(basic_request)

        assert args[0] == "hermes"
        assert args[1] == "--task"
        assert "hello world endpoint" in args[2]
        assert args[3] == "--quiet"

    def test_default_template(self, basic_request):
        cli = GenericCLI(name="test", cli_command="test-cli")
        args = cli._build_args(basic_request)

        assert args[0] == "test-cli"
        assert "hello world endpoint" in args[1]

    def test_name_override(self):
        cli = GenericCLI(name="my-tool", cli_command="my-tool")
        assert cli.name == "my-tool"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestCLIRegistry:

    def test_all_builtin_types_registered(self):
        assert "claude-code" in CLI_REGISTRY
        assert "codex" in CLI_REGISTRY
        assert "gemini" in CLI_REGISTRY
        assert "aider" in CLI_REGISTRY
        assert "generic" in CLI_REGISTRY

    def test_create_cli_claude_code(self):
        cli = create_cli("claude-code", max_turns=5)
        assert isinstance(cli, ClaudeCodeCLI)
        assert cli.default_max_turns == 5

    def test_create_cli_codex(self):
        cli = create_cli("codex", sandbox="read-only")
        assert isinstance(cli, CodexCLI)
        assert cli.sandbox == "read-only"

    def test_create_cli_aider(self):
        cli = create_cli("aider", model="gpt-4")
        assert isinstance(cli, AiderCLI)
        assert cli.model == "gpt-4"

    def test_create_cli_generic(self):
        cli = create_cli("generic", name="hermes", cli_command="hermes")
        assert isinstance(cli, GenericCLI)
        assert cli.cli_command == "hermes"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown CLI type"):
            create_cli("nonexistent")

    def test_register_custom_cli(self):
        class MyCLI(AgentCLI):
            name = "my-cli"
            cli_command = "my-cli"
            def _build_args(self, request):
                return [self.cli_command, request.prompt]

        register_cli("my-cli", MyCLI)
        assert "my-cli" in CLI_REGISTRY

        cli = create_cli("my-cli")
        assert isinstance(cli, MyCLI)

        # Cleanup
        del CLI_REGISTRY["my-cli"]


# ---------------------------------------------------------------------------
# AgentCLI.run() — subprocess execution
# ---------------------------------------------------------------------------


class TestCLIRun:

    @pytest.mark.asyncio
    async def test_run_success(self, basic_request):
        """Successful CLI execution returns parsed response."""
        cli = ClaudeCodeCLI()

        with patch("src.agents.agent_cli.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(
                b'{"result": "Done"}',
                b"",
            ))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            response = await cli.run(basic_request)

        assert response.success
        assert response.output == "Done"

    @pytest.mark.asyncio
    async def test_run_failure(self, basic_request):
        """Failed CLI execution returns error response."""
        cli = ClaudeCodeCLI()

        with patch("src.agents.agent_cli.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(
                b"",
                b"API key invalid",
            ))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            response = await cli.run(basic_request)

        assert not response.success
        assert "API key invalid" in response.error

    @pytest.mark.asyncio
    async def test_run_timeout(self, basic_request):
        """CLI timeout returns error response."""
        basic_request.timeout = 0.001  # Tiny timeout
        cli = ClaudeCodeCLI()

        with patch("src.agents.agent_cli.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=lambda: None)

            # Simulate timeout by making wait_for raise
            with patch("src.agents.agent_cli.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                mock_exec.return_value = mock_proc

                response = await cli.run(basic_request)

        assert not response.success
        assert "timed out" in response.error

    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    async def test_run_binary_not_found(self, basic_request):
        """Missing CLI binary returns helpful error."""
        cli = ClaudeCodeCLI()

        with patch("src.agents.agent_cli.asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            response = await cli.run(basic_request)

        assert not response.success
        assert "not found" in response.error
        assert "claude-code" in response.error


# ---------------------------------------------------------------------------
# AgentCLI — prompt building
# ---------------------------------------------------------------------------


class TestPromptBuilding:

    def test_prompt_without_context(self, tmp_path):
        cli = ClaudeCodeCLI()
        request = CLIRequest(prompt="Do something", work_dir=tmp_path)
        assert cli._build_prompt(request) == "Do something"

    def test_prompt_with_context(self, basic_request):
        cli = ClaudeCodeCLI()
        prompt = cli._build_prompt(basic_request)
        assert "This is a FastAPI project" in prompt
        assert "hello world endpoint" in prompt
        # Context comes before prompt
        assert prompt.index("FastAPI") < prompt.index("hello world")


# ---------------------------------------------------------------------------
# CodingAgent integration with CLI
# ---------------------------------------------------------------------------


class TestCodingAgentCLIIntegration:

    def test_default_cli_from_bundle(self):
        """CodingAgent uses bundle.cli_type to resolve CLI adapter."""
        from src.contracts.coding_bundle import CodingBundle

        bundle = CodingBundle(
            task_id="test-001",
            objective="Test",
            callback_url="http://x",
            repo_url="http://x",
            cli_type="codex",
        )
        from src.agents.coding_agent import CodingAgent
        agent = CodingAgent(bundle=bundle)
        assert isinstance(agent.cli, CodexCLI)

    def test_injected_cli_overrides_bundle(self):
        """Directly injected CLI takes precedence over bundle.cli_type."""
        from src.contracts.coding_bundle import CodingBundle
        from src.agents.coding_agent import CodingAgent

        bundle = CodingBundle(
            task_id="test-002",
            objective="Test",
            callback_url="http://x",
            repo_url="http://x",
            cli_type="codex",  # Bundle says codex
        )
        aider = AiderCLI(model="gpt-4")
        agent = CodingAgent(bundle=bundle, cli=aider)  # But we inject aider
        assert isinstance(agent.cli, AiderCLI)

    def test_cli_args_passed_through(self):
        """Bundle's cli_args are passed to CLI adapter constructor."""
        from src.contracts.coding_bundle import CodingBundle
        from src.agents.coding_agent import CodingAgent

        bundle = CodingBundle(
            task_id="test-003",
            objective="Test",
            callback_url="http://x",
            repo_url="http://x",
            cli_type="claude-code",
            cli_args={"max_turns": 15},
        )
        agent = CodingAgent(bundle=bundle)
        assert isinstance(agent.cli, ClaudeCodeCLI)
        assert agent.cli.default_max_turns == 15

    def test_implementation_prompt_includes_criteria(self):
        """The prompt sent to CLI includes acceptance criteria and scope."""
        from src.contracts.coding_bundle import CodingBundle
        from src.agents.coding_agent import CodingAgent

        bundle = CodingBundle(
            task_id="test-004",
            objective="Add auth endpoint",
            callback_url="http://x",
            repo_url="http://x",
            acceptance_criteria=["Returns JWT", "Handles 401"],
            focus_paths=["src/auth/"],
            protected_paths=[".github/"],
        )
        agent = CodingAgent(bundle=bundle)
        prompt = agent._build_implementation_prompt({
            "focus_paths": bundle.focus_paths,
            "protected_paths": bundle.protected_paths,
        })

        assert "Add auth endpoint" in prompt
        assert "Returns JWT" in prompt
        assert "src/auth/" in prompt
        assert ".github/" in prompt
        assert "Do NOT modify" in prompt


import asyncio
