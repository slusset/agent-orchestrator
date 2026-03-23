"""
Integration tests: Credential passthrough from server boot → graph → agent.

Verifies that credentials resolved at boot time flow correctly through
the full dispatch pipeline:

  Server.boot()
    → CredentialProvider resolves .env / env vars
    → ResolutionResult cached in server
  Server.start_story()
    → per-role env dicts injected into graph context
    → dispatch_coding reads context["_resolved_credentials"]["coding"]
    → CodingBundle.resolved_env populated
    → CodingAgent merges resolved_env into CLI kwargs
    → AgentCLI.extra_env carries secrets to subprocess

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-resolves-credentials.md
  Feature: specs/features/orchestration/pa-resolves-credentials.feature
  Domain Model: specs/models/project/credentials.model.yaml
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.contracts.coding_bundle import CodingBundle
from src.contracts.credentials import (
    ChainCredentialProvider,
    CredentialManifest,
    CredentialProvider,
    CredentialSpec,
    CredentialSource,
    DotEnvCredentialProvider,
    EnvCredentialProvider,
    ResolutionResult,
    StaticCredentialProvider,
    load_credential_manifest,
    validate_credentials,
)
from src.contracts.task_bundle import TaskBundle
from src.agents.coding_agent import CodingAgent
from src.agents.agent_cli import AgentCLI, CLIRequest, CLIResponse, create_cli
from src.orchestrator.dispatcher import AgentDispatcher
from src.orchestrator.graph import _resolved_env_for_role
from src.orchestrator.server import OrchestratorServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manifest_with_roles():
    """A manifest that exercises role-based filtering."""
    return CredentialManifest(credentials=[
        CredentialSpec(
            name="ANTHROPIC_API_KEY",
            required=True,
            source=CredentialSource.ENV,
            description="LLM API key",
            roles=["coding", "pr", "uat"],
        ),
        CredentialSpec(
            name="GITHUB_TOKEN",
            required=True,
            source=CredentialSource.ENV,
            description="GitHub API token",
            roles=["coding", "pr"],
        ),
        CredentialSpec(
            name="JIRA_TOKEN",
            required=False,
            source=CredentialSource.ENV,
            description="JIRA API token",
            roles=["devops"],
        ),
        CredentialSpec(
            name="SHARED_SECRET",
            required=True,
            source=CredentialSource.ENV,
            description="Shared across all roles",
            roles=[],  # empty = all roles
        ),
    ])


@pytest.fixture
def static_provider():
    """A static provider with known test values."""
    return StaticCredentialProvider({
        "ANTHROPIC_API_KEY": "sk-ant-test-1234",
        "GITHUB_TOKEN": "ghp-test-5678",
        "JIRA_TOKEN": "jira-test-9999",
        "SHARED_SECRET": "shared-abc",
    })


@pytest.fixture
def manifest_yaml(tmp_path):
    """Write a credential manifest to a temp file and return the path."""
    manifest_file = tmp_path / ".pm" / "credentials.yaml"
    manifest_file.parent.mkdir(parents=True)
    manifest_file.write_text(textwrap.dedent("""\
        credentials:
          - name: ANTHROPIC_API_KEY
            required: true
            source: env
            description: LLM key
            roles: [coding, pr, uat]
          - name: GITHUB_TOKEN
            required: true
            source: env
            description: GitHub token
            roles: [coding, pr]
          - name: JIRA_TOKEN
            required: false
            source: env
            description: JIRA token
            roles: [devops]
          - name: SHARED_SECRET
            required: true
            source: env
            description: All-role secret
            roles: []
    """))
    return manifest_file


@pytest.fixture
def dotenv_file(tmp_path):
    """Write a .env file and return the path."""
    env_file = tmp_path / ".env"
    env_file.write_text(textwrap.dedent("""\
        ANTHROPIC_API_KEY=sk-ant-from-dotenv
        GITHUB_TOKEN=ghp-from-dotenv
        SHARED_SECRET=shared-from-dotenv
    """))
    return env_file


# ---------------------------------------------------------------------------
# Phase 1: Boot — manifest loads and resolves credentials
# ---------------------------------------------------------------------------


class TestBootCredentialResolution:
    """Feature: PA resolves credentials before dispatching to agents — @boot."""

    @pytest.mark.asyncio
    async def test_boot_loads_manifest_and_resolves(
        self, manifest_yaml, static_provider
    ):
        """Scenario: PA validates credentials at boot with all required present."""
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        result = await server.boot()

        assert result is not None
        assert result.ok
        assert "ANTHROPIC_API_KEY" in result.resolved
        assert "GITHUB_TOKEN" in result.resolved
        assert "SHARED_SECRET" in result.resolved

    @pytest.mark.asyncio
    async def test_boot_no_manifest_returns_none(self, tmp_path, static_provider):
        """Scenario: PA operates without credential manifest."""
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=tmp_path / "does-not-exist.yaml",
        )
        result = await server.boot()

        assert result is None
        assert server.get_resolved_env() == {}

    @pytest.mark.asyncio
    async def test_boot_warns_on_missing_required(self, manifest_yaml):
        """Scenario: PA warns when required credentials are missing."""
        # Provider that only has SHARED_SECRET
        partial_provider = StaticCredentialProvider({"SHARED_SECRET": "x"})
        server = OrchestratorServer(
            transport="log",
            credential_provider=partial_provider,
            manifest_path=manifest_yaml,
        )
        result = await server.boot()

        assert result is not None
        assert not result.ok
        assert "ANTHROPIC_API_KEY" in result.missing
        assert "GITHUB_TOKEN" in result.missing


# ---------------------------------------------------------------------------
# Phase 2: Role filtering — each role gets only its credentials
# ---------------------------------------------------------------------------


class TestRoleFiltering:
    """Feature: PA resolves credentials — @role-filter."""

    @pytest.mark.asyncio
    async def test_coding_role_gets_correct_credentials(
        self, manifest_yaml, static_provider
    ):
        """Scenario: Coding agent receives only coding-role credentials."""
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        await server.boot()
        env = server.get_resolved_env("coding")

        assert "ANTHROPIC_API_KEY" in env
        assert "GITHUB_TOKEN" in env
        assert "SHARED_SECRET" in env  # empty roles = all roles
        assert "JIRA_TOKEN" not in env  # devops-only

    @pytest.mark.asyncio
    async def test_devops_role_gets_correct_credentials(
        self, manifest_yaml, static_provider
    ):
        """Scenario: DevOps agent gets only devops + shared credentials."""
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        await server.boot()
        env = server.get_resolved_env("devops")

        assert "JIRA_TOKEN" in env
        assert "SHARED_SECRET" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "GITHUB_TOKEN" not in env

    @pytest.mark.asyncio
    async def test_uat_role_gets_correct_credentials(
        self, manifest_yaml, static_provider
    ):
        """Scenario: UAT agent receives only UAT-role credentials."""
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        await server.boot()
        env = server.get_resolved_env("uat")

        assert "ANTHROPIC_API_KEY" in env
        assert "SHARED_SECRET" in env
        assert "GITHUB_TOKEN" not in env
        assert "JIRA_TOKEN" not in env


# ---------------------------------------------------------------------------
# Phase 3: Graph context injection — credentials flow into dispatch nodes
# ---------------------------------------------------------------------------


class TestGraphContextInjection:
    """Feature: Graph dispatch nodes read role-specific credentials from context."""

    def test_resolved_env_for_role_extracts_correctly(self):
        """Scenario: _resolved_env_for_role reads from graph context."""
        context = {
            "_resolved_credentials": {
                "coding": {"ANTHROPIC_API_KEY": "sk-test", "GITHUB_TOKEN": "ghp-test"},
                "pr": {"ANTHROPIC_API_KEY": "sk-test"},
                "uat": {"ANTHROPIC_API_KEY": "sk-test"},
                "devops": {"JIRA_TOKEN": "jira-test"},
            }
        }

        coding_env = _resolved_env_for_role(context, "coding")
        assert coding_env == {"ANTHROPIC_API_KEY": "sk-test", "GITHUB_TOKEN": "ghp-test"}

        devops_env = _resolved_env_for_role(context, "devops")
        assert devops_env == {"JIRA_TOKEN": "jira-test"}

    def test_resolved_env_returns_empty_when_no_credentials(self):
        """Scenario: Empty context yields empty env dict (backward-compatible)."""
        assert _resolved_env_for_role({}, "coding") == {}
        assert _resolved_env_for_role({"_resolved_credentials": {}}, "coding") == {}

    @pytest.mark.asyncio
    async def test_start_story_injects_per_role_envs_into_context(
        self, manifest_yaml, static_provider
    ):
        """
        Scenario: start_story() populates context['_resolved_credentials']
        with per-role env dicts before the graph runs.
        """
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        await server.boot()

        # Capture the state that the graph receives by mocking graph.invoke
        captured_state = {}

        def capture_invoke(state, config):
            captured_state.update(state)
            # Return minimal state so _dispatch_pending_tasks doesn't crash
            return {"tasks": [], "messages": [], "current_story": {}, "context": state.get("context", {})}

        server.graph.invoke = capture_invoke

        await server.start_story(
            message="Test credential injection",
            story={"objective": "Test"},
        )

        ctx = captured_state.get("context", {})
        resolved = ctx.get("_resolved_credentials", {})

        # Coding role should have ANTHROPIC_API_KEY, GITHUB_TOKEN, SHARED_SECRET
        assert "ANTHROPIC_API_KEY" in resolved["coding"]
        assert "GITHUB_TOKEN" in resolved["coding"]
        assert "SHARED_SECRET" in resolved["coding"]
        assert "JIRA_TOKEN" not in resolved["coding"]

        # DevOps should have JIRA_TOKEN, SHARED_SECRET
        assert "JIRA_TOKEN" in resolved["devops"]
        assert "SHARED_SECRET" in resolved["devops"]
        assert "GITHUB_TOKEN" not in resolved["devops"]


# ---------------------------------------------------------------------------
# Phase 4: CodingBundle → CodingAgent → AgentCLI.extra_env
# ---------------------------------------------------------------------------


class TestCodingAgentCredentialInjection:
    """Feature: CodingAgent passes resolved_env to CLI subprocess."""

    def test_coding_bundle_carries_resolved_env(self):
        """Scenario: CodingBundle.resolved_env is populated and accessible."""
        bundle = CodingBundle(
            objective="Test feature",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env={"ANTHROPIC_API_KEY": "sk-test-value"},
        )

        assert bundle.resolved_env == {"ANTHROPIC_API_KEY": "sk-test-value"}

    def test_resolved_env_excluded_from_json(self):
        """Scenario: resolved_env is excluded from serialization."""
        bundle = CodingBundle(
            objective="Test feature",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env={"SECRET": "super-secret"},
        )

        serialized = bundle.model_dump(mode="json")
        assert "resolved_env" not in serialized
        assert "super-secret" not in str(serialized)

    def test_coding_agent_merges_resolved_env_into_cli(self):
        """
        Scenario: CodingAgent passes credentials to CLI subprocess.

        When CodingAgent is constructed with a CodingBundle that has
        resolved_env, the CLI adapter receives those values as extra_env.
        """
        bundle = CodingBundle(
            objective="Implement auth",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env={
                "ANTHROPIC_API_KEY": "sk-ant-test-1234",
                "GITHUB_TOKEN": "ghp-test-5678",
            },
        )

        # Let CodingAgent create its own CLI from bundle config
        # (don't inject a CLI — we want to verify the auto-creation path)
        agent = CodingAgent(bundle=bundle)

        # The CLI should have our credentials in extra_env
        assert agent.cli.extra_env.get("ANTHROPIC_API_KEY") == "sk-ant-test-1234"
        assert agent.cli.extra_env.get("GITHUB_TOKEN") == "ghp-test-5678"

    def test_coding_agent_with_empty_resolved_env(self):
        """Scenario: CodingAgent works fine when no credentials are provided."""
        bundle = CodingBundle(
            objective="Simple task",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
        )

        agent = CodingAgent(bundle=bundle)
        # Should not crash; extra_env may be empty or from cli_args defaults
        assert isinstance(agent.cli.extra_env, dict)

    def test_injected_cli_preserves_existing_env(self):
        """
        Scenario: When CLI already has env vars, resolved_env merges
        without overwriting (resolved_env takes precedence).
        """
        bundle = CodingBundle(
            objective="Test",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            cli_args={"env": {"EXISTING_VAR": "keep-me"}},
            resolved_env={
                "ANTHROPIC_API_KEY": "sk-new",
                "EXISTING_VAR": "overwrite-me",
            },
        )

        agent = CodingAgent(bundle=bundle)

        # resolved_env should merge over existing cli_args env
        assert agent.cli.extra_env["ANTHROPIC_API_KEY"] == "sk-new"
        # resolved_env takes precedence via dict merge order
        assert agent.cli.extra_env["EXISTING_VAR"] == "overwrite-me"


# ---------------------------------------------------------------------------
# Phase 4b: Serialization gap — credentials survive TaskRecord round-trip
# ---------------------------------------------------------------------------


class TestCredentialSurvivesSerialization:
    """
    Regression: dispatch_coding serializes bundle via model_dump(mode='json')
    which strips resolved_env (exclude=True). The server re-injects credentials
    from its cache before the dispatcher creates the agent.
    """

    @pytest.mark.asyncio
    async def test_server_reinjects_credentials_before_dispatch(
        self, manifest_yaml, static_provider
    ):
        """
        Scenario: Credentials survive the TaskRecord serialization gap.

        1. dispatch_coding creates bundle with resolved_env
        2. bundle.model_dump(mode='json') strips resolved_env
        3. TaskRecord stores the stripped dict
        4. Server._dispatch_pending_tasks re-injects from cache
        5. Dispatcher reads _resolved_env and restores on the bundle
        """
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        await server.boot()

        # Simulate what dispatch_coding does: create + serialize
        bundle = CodingBundle(
            objective="Test serialization gap",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env=server.get_resolved_env("coding"),
        )
        serialized = bundle.model_dump(mode="json")
        assert "resolved_env" not in serialized  # Confirm it's stripped

        # Simulate TaskRecord as stored in graph state
        task_record = {
            "task_id": bundle.task_id,
            "agent_type": "coding",
            "bundle": serialized,
            "status": "dispatched",
            "last_update": None,
            "result": None,
            "pr_url": None,
        }

        # Server re-injects credentials (as _dispatch_pending_tasks does)
        role = task_record["agent_type"]
        resolved = server.get_resolved_env(role)
        task_record["_resolved_env"] = resolved

        # Dispatcher deserializes and re-injects
        deserialized = CodingBundle.model_validate(task_record["bundle"])
        assert deserialized.resolved_env == {}  # Still empty after deserialization

        # Apply the re-injection (as dispatcher._dispatch_local does)
        deserialized.resolved_env = task_record["_resolved_env"]

        # Now create agent — credentials should flow to CLI
        agent = CodingAgent(bundle=deserialized)
        assert agent.cli.extra_env["ANTHROPIC_API_KEY"] == "sk-ant-test-1234"
        assert agent.cli.extra_env["GITHUB_TOKEN"] == "ghp-test-5678"
        assert agent.cli.extra_env["SHARED_SECRET"] == "shared-abc"
        assert "JIRA_TOKEN" not in agent.cli.extra_env


# ---------------------------------------------------------------------------
# Phase 5: Provider chain — DotEnv → Env with priority fallback
# ---------------------------------------------------------------------------


class TestProviderChainIntegration:
    """Feature: Chain provider resolves with priority fallback."""

    @pytest.mark.asyncio
    async def test_chain_dotenv_then_env(self, dotenv_file):
        """
        Scenario: Chain provider resolves with priority fallback.

        DotEnv has ANTHROPIC_API_KEY; env has ANTHROPIC_API_KEY too.
        DotEnv should win (first in chain).
        """
        # Set an env var that should be shadowed by dotenv
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-from-os-env", "JIRA_TOKEN": "jira-from-env"}):
            chain = ChainCredentialProvider([
                DotEnvCredentialProvider(dotenv_file),
                EnvCredentialProvider(),
            ])

            manifest = CredentialManifest(credentials=[
                CredentialSpec(name="ANTHROPIC_API_KEY", required=True),
                CredentialSpec(name="JIRA_TOKEN", required=True),
            ])

            result = await chain.resolve(manifest)

        assert result.ok
        # DotEnv had ANTHROPIC_API_KEY — it should win
        assert result.resolved["ANTHROPIC_API_KEY"].value == "sk-ant-from-dotenv"
        assert result.resolved["ANTHROPIC_API_KEY"].source == "dotenv"
        # DotEnv didn't have JIRA_TOKEN — falls through to EnvProvider
        assert result.resolved["JIRA_TOKEN"].value == "jira-from-env"
        assert result.resolved["JIRA_TOKEN"].source == "env"

    @pytest.mark.asyncio
    async def test_chain_falls_through_on_missing(self, dotenv_file):
        """
        Scenario: Chain provider falls through on missing key.

        When dotenv doesn't have a key, env provider is tried next.
        """
        with patch.dict(os.environ, {"MISSING_FROM_DOTENV": "found-in-env"}):
            chain = ChainCredentialProvider([
                DotEnvCredentialProvider(dotenv_file),
                EnvCredentialProvider(),
            ])

            manifest = CredentialManifest(credentials=[
                CredentialSpec(name="MISSING_FROM_DOTENV", required=True),
            ])

            result = await chain.resolve(manifest)

        assert result.ok
        assert result.resolved["MISSING_FROM_DOTENV"].value == "found-in-env"
        assert result.resolved["MISSING_FROM_DOTENV"].source == "env"


# ---------------------------------------------------------------------------
# Phase 6: End-to-end — boot through dispatch to agent env
# ---------------------------------------------------------------------------


class TestEndToEndCredentialFlow:
    """
    Full pipeline: Server boots → resolves credentials → starts story →
    graph injects into CodingBundle → CodingAgent gets env.
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_server_to_agent_env(
        self, manifest_yaml, static_provider
    ):
        """
        End-to-end: credentials flow from manifest → provider → server →
        graph context → CodingBundle.resolved_env → CodingAgent.cli.extra_env.

        Uses the real server.boot() and get_resolved_env(), then manually
        constructs the bundle as the dispatch node would.
        """
        # 1. Boot server (real credential resolution)
        server = OrchestratorServer(
            transport="log",
            credential_provider=static_provider,
            manifest_path=manifest_yaml,
        )
        result = await server.boot()
        assert result.ok

        # 2. Get per-role env (as start_story would)
        coding_env = server.get_resolved_env("coding")
        assert "ANTHROPIC_API_KEY" in coding_env
        assert "GITHUB_TOKEN" in coding_env
        assert "SHARED_SECRET" in coding_env
        assert "JIRA_TOKEN" not in coding_env

        # 3. Simulate what dispatch_coding does: create bundle with resolved_env
        bundle = CodingBundle(
            objective="Implement user auth",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env=coding_env,
        )

        # 4. Simulate what CodingAgent does: pass env to CLI
        agent = CodingAgent(bundle=bundle)

        # 5. Verify the CLI received the credentials
        assert agent.cli.extra_env["ANTHROPIC_API_KEY"] == "sk-ant-test-1234"
        assert agent.cli.extra_env["GITHUB_TOKEN"] == "ghp-test-5678"
        assert agent.cli.extra_env["SHARED_SECRET"] == "shared-abc"
        assert "JIRA_TOKEN" not in agent.cli.extra_env

    @pytest.mark.asyncio
    async def test_full_pipeline_with_dotenv_provider(
        self, manifest_yaml, dotenv_file
    ):
        """
        Same pipeline but using DotEnvProvider — proves .env files
        flow through to agent subprocess env.
        """
        provider = DotEnvCredentialProvider(dotenv_file)
        server = OrchestratorServer(
            transport="log",
            credential_provider=provider,
            manifest_path=manifest_yaml,
        )
        result = await server.boot()

        # JIRA_TOKEN not in .env file, so it'll be missing (optional)
        assert "ANTHROPIC_API_KEY" in result.resolved
        assert "GITHUB_TOKEN" in result.resolved

        coding_env = server.get_resolved_env("coding")
        bundle = CodingBundle(
            objective="Test with dotenv",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env=coding_env,
        )
        agent = CodingAgent(bundle=bundle)

        assert agent.cli.extra_env["ANTHROPIC_API_KEY"] == "sk-ant-from-dotenv"
        assert agent.cli.extra_env["GITHUB_TOKEN"] == "ghp-from-dotenv"

    @pytest.mark.asyncio
    async def test_full_pipeline_with_chain_provider(
        self, manifest_yaml, dotenv_file
    ):
        """
        Chain provider: DotEnv → Env. Proves fallback works end-to-end.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "jira-from-env-var"}):
            chain = ChainCredentialProvider([
                DotEnvCredentialProvider(dotenv_file),
                EnvCredentialProvider(),
            ])
            server = OrchestratorServer(
                transport="log",
                credential_provider=chain,
                manifest_path=manifest_yaml,
            )
            result = await server.boot()

        assert result.ok

        # Coding role
        coding_env = server.get_resolved_env("coding")
        assert coding_env["ANTHROPIC_API_KEY"] == "sk-ant-from-dotenv"  # from dotenv

        # DevOps role — JIRA_TOKEN fell through to env
        devops_env = server.get_resolved_env("devops")
        assert devops_env["JIRA_TOKEN"] == "jira-from-env-var"  # from os.environ

        # Verify agent gets correct env
        bundle = CodingBundle(
            objective="Chain test",
            callback_url="http://localhost:8000/callback",
            repo_url="git@github.com:test/repo.git",
            resolved_env=coding_env,
        )
        agent = CodingAgent(bundle=bundle)
        assert agent.cli.extra_env["ANTHROPIC_API_KEY"] == "sk-ant-from-dotenv"
        assert "JIRA_TOKEN" not in agent.cli.extra_env  # not a coding credential
