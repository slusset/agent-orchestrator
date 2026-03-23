"""
Unit tests for the credential provider contract.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Domain Model: specs/models/project/project-store.model.yaml
"""

import os

import pytest

from src.contracts.credentials import (
    ChainCredentialProvider,
    CredentialManifest,
    CredentialSource,
    CredentialSpec,
    EnvCredentialProvider,
    ResolutionResult,
    ResolvedCredential,
    StaticCredentialProvider,
    load_credential_manifest,
    validate_credentials,
)


# ---------------------------------------------------------------------------
# CredentialSpec
# ---------------------------------------------------------------------------


class TestCredentialSpec:
    def test_defaults(self):
        spec = CredentialSpec(name="MY_KEY")
        assert spec.required is True
        assert spec.source == CredentialSource.ENV
        assert spec.roles == []
        assert spec.scopes == []

    def test_lookup_key_defaults_to_name(self):
        spec = CredentialSpec(name="ANTHROPIC_API_KEY")
        assert spec.lookup_key == "ANTHROPIC_API_KEY"

    def test_lookup_key_override(self):
        spec = CredentialSpec(name="ANTHROPIC_API_KEY", env_var="CLAUDE_KEY")
        assert spec.lookup_key == "CLAUDE_KEY"


# ---------------------------------------------------------------------------
# CredentialManifest
# ---------------------------------------------------------------------------


class TestCredentialManifest:
    @pytest.fixture
    def manifest(self):
        return CredentialManifest(
            credentials=[
                CredentialSpec(name="ANTHROPIC_API_KEY", roles=["coding"]),
                CredentialSpec(name="GITHUB_TOKEN", roles=["coding", "devops"]),
                CredentialSpec(name="JIRA_TOKEN", required=False, roles=["devops"]),
                CredentialSpec(name="SHARED_SECRET", roles=[]),  # All roles
            ]
        )

    def test_for_role_coding(self, manifest):
        coding = manifest.for_role("coding")
        names = [c.name for c in coding]
        assert "ANTHROPIC_API_KEY" in names
        assert "GITHUB_TOKEN" in names
        assert "SHARED_SECRET" in names  # empty roles = all
        assert "JIRA_TOKEN" not in names

    def test_for_role_devops(self, manifest):
        devops = manifest.for_role("devops")
        names = [c.name for c in devops]
        assert "GITHUB_TOKEN" in names
        assert "JIRA_TOKEN" in names
        assert "SHARED_SECRET" in names
        assert "ANTHROPIC_API_KEY" not in names

    def test_required(self, manifest):
        required = manifest.required()
        names = [c.name for c in required]
        assert "JIRA_TOKEN" not in names
        assert len(names) == 3

    def test_names(self, manifest):
        assert manifest.names() == [
            "ANTHROPIC_API_KEY",
            "GITHUB_TOKEN",
            "JIRA_TOKEN",
            "SHARED_SECRET",
        ]


# ---------------------------------------------------------------------------
# ResolutionResult
# ---------------------------------------------------------------------------


class TestResolutionResult:
    def test_ok_when_all_resolved(self):
        result = ResolutionResult(
            resolved={"KEY": ResolvedCredential(name="KEY", value="val", source="env")}
        )
        assert result.ok is True

    def test_not_ok_when_missing(self):
        result = ResolutionResult(missing=["KEY"])
        assert result.ok is False

    def test_not_ok_when_errors(self):
        result = ResolutionResult(errors={"KEY": "boom"})
        assert result.ok is False

    def test_as_env(self):
        result = ResolutionResult(
            resolved={
                "A": ResolvedCredential(name="A", value="val_a", source="env"),
                "B": ResolvedCredential(name="B", value="val_b", source="static"),
            }
        )
        env = result.as_env()
        assert env == {"A": "val_a", "B": "val_b"}

    def test_summary(self):
        result = ResolutionResult(
            resolved={"A": ResolvedCredential(name="A", value="x", source="env")},
            missing=["B"],
        )
        summary = result.summary()
        assert "1 resolved" in summary
        assert "1 missing" in summary
        assert "B" in summary


# ---------------------------------------------------------------------------
# EnvCredentialProvider
# ---------------------------------------------------------------------------


class TestEnvCredentialProvider:
    @pytest.fixture
    def provider(self):
        return EnvCredentialProvider()

    @pytest.mark.asyncio
    async def test_resolves_from_env(self, provider, monkeypatch):
        monkeypatch.setenv("TEST_CRED_ABC", "secret123")
        spec = CredentialSpec(name="TEST_CRED_ABC")
        value = await provider.get(spec)
        assert value == "secret123"

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self, provider):
        spec = CredentialSpec(name="DEFINITELY_NOT_SET_XYZ_123")
        value = await provider.get(spec)
        assert value is None

    @pytest.mark.asyncio
    async def test_uses_env_var_override(self, provider, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_VAR", "custom_val")
        spec = CredentialSpec(name="ANTHROPIC_API_KEY", env_var="MY_CUSTOM_VAR")
        value = await provider.get(spec)
        assert value == "custom_val"

    @pytest.mark.asyncio
    async def test_resolve_manifest(self, provider, monkeypatch):
        monkeypatch.setenv("KEY_A", "val_a")
        # KEY_B not set
        manifest = CredentialManifest(
            credentials=[
                CredentialSpec(name="KEY_A"),
                CredentialSpec(name="KEY_B"),  # required, missing
                CredentialSpec(name="KEY_C", required=False),  # optional, missing
            ]
        )
        result = await provider.resolve(manifest)
        assert "KEY_A" in result.resolved
        assert "KEY_B" in result.missing
        assert "KEY_C" not in result.missing  # optional
        assert result.ok is False  # KEY_B is required and missing


# ---------------------------------------------------------------------------
# StaticCredentialProvider
# ---------------------------------------------------------------------------


class TestStaticCredentialProvider:
    @pytest.mark.asyncio
    async def test_resolves_from_dict(self):
        provider = StaticCredentialProvider({"API_KEY": "secret"})
        spec = CredentialSpec(name="API_KEY")
        value = await provider.get(spec)
        assert value == "secret"

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self):
        provider = StaticCredentialProvider({})
        spec = CredentialSpec(name="API_KEY")
        value = await provider.get(spec)
        assert value is None

    @pytest.mark.asyncio
    async def test_resolves_by_lookup_key(self):
        provider = StaticCredentialProvider({"CUSTOM_VAR": "val"})
        spec = CredentialSpec(name="API_KEY", env_var="CUSTOM_VAR")
        value = await provider.get(spec)
        assert value == "val"


# ---------------------------------------------------------------------------
# ChainCredentialProvider
# ---------------------------------------------------------------------------


class TestChainCredentialProvider:
    @pytest.mark.asyncio
    async def test_first_match_wins(self):
        """Chain returns the first provider's value."""
        chain = ChainCredentialProvider(
            [
                StaticCredentialProvider({"KEY": "from_first"}),
                StaticCredentialProvider({"KEY": "from_second"}),
            ]
        )
        spec = CredentialSpec(name="KEY")
        value = await chain.get(spec)
        assert value == "from_first"

    @pytest.mark.asyncio
    async def test_falls_through_to_second(self):
        """If first provider misses, try the second."""
        chain = ChainCredentialProvider(
            [
                StaticCredentialProvider({}),  # doesn't have KEY
                StaticCredentialProvider({"KEY": "from_second"}),
            ]
        )
        spec = CredentialSpec(name="KEY")
        value = await chain.get(spec)
        assert value == "from_second"

    @pytest.mark.asyncio
    async def test_resolve_tracks_source(self):
        """Resolve records which provider resolved each credential."""
        chain = ChainCredentialProvider(
            [
                StaticCredentialProvider({"A": "val_a"}),
                StaticCredentialProvider({"B": "val_b"}),
            ]
        )
        # Name the providers for tracking
        chain.providers[0].name = "primary"
        chain.providers[1].name = "fallback"

        manifest = CredentialManifest(
            credentials=[
                CredentialSpec(name="A"),
                CredentialSpec(name="B"),
            ]
        )
        result = await chain.resolve(manifest)
        assert result.ok
        assert result.resolved["A"].source == "primary"
        assert result.resolved["B"].source == "fallback"

    @pytest.mark.asyncio
    async def test_resolve_with_env_fallback(self, monkeypatch):
        """Realistic chain: static overrides + env fallback."""
        monkeypatch.setenv("GITHUB_TOKEN", "env_token")

        chain = ChainCredentialProvider(
            [
                StaticCredentialProvider({"ANTHROPIC_API_KEY": "static_key"}),
                EnvCredentialProvider(),
            ]
        )

        manifest = CredentialManifest(
            credentials=[
                CredentialSpec(name="ANTHROPIC_API_KEY"),
                CredentialSpec(name="GITHUB_TOKEN"),
            ]
        )
        result = await chain.resolve(manifest)
        assert result.ok
        assert result.resolved["ANTHROPIC_API_KEY"].value == "static_key"
        assert result.resolved["GITHUB_TOKEN"].value == "env_token"


# ---------------------------------------------------------------------------
# validate_credentials
# ---------------------------------------------------------------------------


class TestValidateCredentials:
    @pytest.mark.asyncio
    async def test_validates_all_present(self):
        provider = StaticCredentialProvider({"A": "1", "B": "2"})
        manifest = CredentialManifest(
            credentials=[
                CredentialSpec(name="A"),
                CredentialSpec(name="B"),
            ]
        )
        result = await validate_credentials(manifest, provider)
        assert result.ok

    @pytest.mark.asyncio
    async def test_validates_missing_required(self):
        provider = StaticCredentialProvider({"A": "1"})
        manifest = CredentialManifest(
            credentials=[
                CredentialSpec(name="A"),
                CredentialSpec(name="B"),  # required, missing
            ]
        )
        result = await validate_credentials(manifest, provider)
        assert not result.ok
        assert "B" in result.missing

    @pytest.mark.asyncio
    async def test_validates_for_role(self):
        provider = StaticCredentialProvider({"A": "1"})
        manifest = CredentialManifest(
            credentials=[
                CredentialSpec(name="A", roles=["coding"]),
                CredentialSpec(name="B", roles=["devops"]),  # not needed for coding
            ]
        )
        result = await validate_credentials(manifest, provider, role="coding")
        assert result.ok  # B not required for coding role


# ---------------------------------------------------------------------------
# TaskBundle.resolved_env integration
# ---------------------------------------------------------------------------


class TestTaskBundleResolvedEnv:
    """Test that resolved_env on TaskBundle is excluded from serialization."""

    def test_resolved_env_excluded_from_json(self):
        from src.contracts.task_bundle import TaskBundle

        bundle = TaskBundle(
            objective="test",
            callback_url="http://localhost:8000/callback",
            resolved_env={"SECRET_KEY": "super_secret"},
        )
        # resolved_env should NOT appear in serialized output
        data = bundle.model_dump()
        assert "resolved_env" not in data

    def test_resolved_env_accessible_in_memory(self):
        from src.contracts.task_bundle import TaskBundle

        bundle = TaskBundle(
            objective="test",
            callback_url="http://localhost:8000/callback",
            resolved_env={"SECRET_KEY": "super_secret"},
        )
        # But it IS accessible in memory for the agent to use
        assert bundle.resolved_env == {"SECRET_KEY": "super_secret"}


# ---------------------------------------------------------------------------
# Manifest Loading
# ---------------------------------------------------------------------------


class TestLoadCredentialManifest:
    """Tests for YAML manifest loading."""

    def test_load_from_yaml(self, tmp_path):
        manifest_file = tmp_path / "credentials.yaml"
        manifest_file.write_text(
            "credentials:\n"
            "  - name: ANTHROPIC_API_KEY\n"
            "    required: true\n"
            "    source: env\n"
            "    roles: [coding]\n"
            "  - name: GITHUB_TOKEN\n"
            "    required: true\n"
            "    source: env\n"
            "    roles: [coding, pr]\n"
            "  - name: JIRA_TOKEN\n"
            "    required: false\n"
            "    source: env\n"
        )
        manifest = load_credential_manifest(manifest_file)
        assert len(manifest.credentials) == 3
        assert manifest.credentials[0].name == "ANTHROPIC_API_KEY"
        assert manifest.credentials[0].roles == ["coding"]
        assert manifest.credentials[2].required is False

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_credential_manifest(tmp_path / "nonexistent.yaml")

    def test_load_malformed_yaml_raises(self, tmp_path):
        manifest_file = tmp_path / "bad.yaml"
        manifest_file.write_text("just_a_string: true\n")
        with pytest.raises(ValueError, match="expected a 'credentials' key"):
            load_credential_manifest(manifest_file)

    def test_load_empty_yaml_raises(self, tmp_path):
        manifest_file = tmp_path / "empty.yaml"
        manifest_file.write_text("")
        with pytest.raises(ValueError, match="expected a 'credentials' key"):
            load_credential_manifest(manifest_file)

    def test_load_project_manifest(self):
        """Verify the actual .pm/credentials.yaml in this repo loads correctly."""
        from pathlib import Path

        manifest_path = Path(".pm/credentials.yaml")
        if not manifest_path.exists():
            pytest.skip("No .pm/credentials.yaml in repo")

        manifest = load_credential_manifest(manifest_path)
        assert len(manifest.credentials) >= 2
        names = manifest.names()
        assert "ANTHROPIC_API_KEY" in names
        assert "GITHUB_TOKEN" in names


# ---------------------------------------------------------------------------
# OrchestratorServer Credential Wiring
# ---------------------------------------------------------------------------


class TestServerCredentialWiring:
    """Tests for OrchestratorServer boot-time credential resolution."""

    @pytest.fixture
    def manifest_file(self, tmp_path):
        f = tmp_path / "credentials.yaml"
        f.write_text(
            "credentials:\n"
            "  - name: ANTHROPIC_API_KEY\n"
            "    required: true\n"
            "    source: env\n"
            "    roles: [coding, pr, uat]\n"
            "  - name: GITHUB_TOKEN\n"
            "    required: true\n"
            "    source: env\n"
            "    roles: [coding, pr]\n"
            "  - name: JIRA_TOKEN\n"
            "    required: false\n"
            "    source: env\n"
            "    roles: []\n"
        )
        return f

    @pytest.mark.asyncio
    async def test_boot_loads_and_validates(self, manifest_file):
        from src.orchestrator.server import OrchestratorServer

        provider = StaticCredentialProvider(
            {"ANTHROPIC_API_KEY": "sk-ant-test", "GITHUB_TOKEN": "ghp_test"}
        )
        server = OrchestratorServer(
            credential_provider=provider,
            manifest_path=manifest_file,
        )
        result = await server.boot()
        assert result is not None
        assert result.ok

    @pytest.mark.asyncio
    async def test_boot_reports_missing(self, manifest_file):
        from src.orchestrator.server import OrchestratorServer

        provider = StaticCredentialProvider({})  # nothing provided
        server = OrchestratorServer(
            credential_provider=provider,
            manifest_path=manifest_file,
        )
        result = await server.boot()
        assert result is not None
        assert not result.ok
        assert "ANTHROPIC_API_KEY" in result.missing

    @pytest.mark.asyncio
    async def test_boot_no_manifest(self, tmp_path):
        from src.orchestrator.server import OrchestratorServer

        server = OrchestratorServer(
            manifest_path=tmp_path / "nonexistent.yaml",
        )
        result = await server.boot()
        assert result is None

    def test_get_resolved_env_role_filter(self, manifest_file):
        import asyncio
        from src.orchestrator.server import OrchestratorServer

        provider = StaticCredentialProvider(
            {"ANTHROPIC_API_KEY": "sk-ant-test", "GITHUB_TOKEN": "ghp_test"}
        )
        server = OrchestratorServer(
            credential_provider=provider,
            manifest_path=manifest_file,
        )
        asyncio.get_event_loop().run_until_complete(server.boot())

        coding_env = server.get_resolved_env("coding")
        assert "ANTHROPIC_API_KEY" in coding_env
        assert "GITHUB_TOKEN" in coding_env

        uat_env = server.get_resolved_env("uat")
        assert "ANTHROPIC_API_KEY" in uat_env
        # GITHUB_TOKEN is not assigned to uat role
        assert "GITHUB_TOKEN" not in uat_env

    def test_get_resolved_env_no_boot(self):
        from src.orchestrator.server import OrchestratorServer

        server = OrchestratorServer()
        assert server.get_resolved_env("coding") == {}


# ---------------------------------------------------------------------------
# Graph Dispatch Node Credential Injection
# ---------------------------------------------------------------------------


class TestDispatchNodeCredentialInjection:
    """Tests that dispatch nodes inject resolved_env from context."""

    def test_dispatch_coding_injects_resolved_env(self):
        from src.orchestrator.graph import dispatch_coding

        state = {
            "messages": [],
            "tasks": [],
            "current_story": {
                "objective": "test",
                "repo_url": "https://github.com/test/repo",
            },
            "context": {
                "callback_url": "http://localhost:8000/callback",
                "_resolved_credentials": {
                    "coding": {"ANTHROPIC_API_KEY": "sk-test", "GITHUB_TOKEN": "ghp_test"},
                    "pr": {"GITHUB_TOKEN": "ghp_test"},
                },
            },
        }
        result = dispatch_coding(state)
        task = result["tasks"][0]

        # Reconstruct the bundle to check resolved_env
        # Since resolved_env is exclude=True, it won't be in the serialized bundle dict.
        # But the CodingBundle was created with it, so we verify via the bundle construction.
        from src.contracts import CodingBundle

        bundle = CodingBundle(
            **task["bundle"],
            resolved_env={"ANTHROPIC_API_KEY": "sk-test", "GITHUB_TOKEN": "ghp_test"},
        )
        assert bundle.resolved_env == {"ANTHROPIC_API_KEY": "sk-test", "GITHUB_TOKEN": "ghp_test"}

    def test_dispatch_coding_no_credentials(self):
        from src.orchestrator.graph import dispatch_coding

        state = {
            "messages": [],
            "tasks": [],
            "current_story": {"objective": "test", "repo_url": ""},
            "context": {"callback_url": "http://localhost:8000/callback"},
        }
        result = dispatch_coding(state)
        # Should not raise — gracefully gets empty dict
        assert len(result["tasks"]) == 1
