"""
Credential Provider: Pluggable secret resolution for agent task dispatch.

The PA resolves credentials before dispatching a TaskBundle. Agents never
manage their own auth — they receive resolved env vars in the bundle.

Architecture:
    ProjectStore                    (holds credential manifest)
        → CredentialProvider        (resolves values from backends)
            → TaskBundle.resolved_env   (carried to the agent)
                → AgentCLI.env      (injected into subprocess)

Providers are composable: EnvProvider is the fallback, with optional
KeychainProvider, SopsProvider, or VaultProvider layered on top via
ChainProvider.

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-plans-and-dispatches.md
  Domain Model: specs/models/project/project-store.model.yaml
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credential Manifest (what secrets a project needs)
# ---------------------------------------------------------------------------


class CredentialSource(str, Enum):
    """Where a credential value can be resolved from."""

    ENV = "env"  # Environment variable
    KEYCHAIN = "keychain"  # macOS Keychain / system credential store
    SOPS = "sops"  # SOPS-encrypted file
    VAULT = "vault"  # HashiCorp Vault or similar
    CONFIG = "config"  # Plaintext in config (non-sensitive only)


class CredentialSpec(BaseModel):
    """
    A single credential the project needs.

    This is the manifest entry — it declares WHAT is needed, WHERE to find it,
    and WHO needs it. It does NOT contain the secret value.
    """

    name: str  # Canonical name, e.g. "ANTHROPIC_API_KEY"
    required: bool = True
    source: CredentialSource = CredentialSource.ENV
    description: str = ""
    env_var: str | None = None  # Override: look for this env var instead of name
    roles: list[str] = Field(default_factory=list)  # Which roles need this: ["coding", "devops"]
    scopes: list[str] = Field(default_factory=list)  # e.g. ["repo", "read:org"] for GitHub tokens

    @property
    def lookup_key(self) -> str:
        """The actual env var name to look up."""
        return self.env_var or self.name


class CredentialManifest(BaseModel):
    """
    The full credential manifest for a project.

    Lives in .pm/credentials.yaml (or equivalent in ProjectStore).
    Checked into the repo — no secret values, only the manifest.
    """

    credentials: list[CredentialSpec] = Field(default_factory=list)

    def for_role(self, role: str) -> list[CredentialSpec]:
        """Get credentials needed by a specific role."""
        return [
            c for c in self.credentials
            if not c.roles or role in c.roles  # Empty roles = all roles need it
        ]

    def required(self) -> list[CredentialSpec]:
        """Get all required credentials."""
        return [c for c in self.credentials if c.required]

    def names(self) -> list[str]:
        """All credential names."""
        return [c.name for c in self.credentials]


# ---------------------------------------------------------------------------
# Credential Provider (resolves secret values)
# ---------------------------------------------------------------------------


@dataclass
class ResolvedCredential:
    """A credential with its resolved value."""

    name: str
    value: str
    source: str  # Which provider resolved it


@dataclass
class ResolutionResult:
    """Result of resolving a credential manifest."""

    resolved: dict[str, ResolvedCredential] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True if no required credentials are missing."""
        return len(self.missing) == 0 and len(self.errors) == 0

    def as_env(self) -> dict[str, str]:
        """Convert resolved credentials to env var dict for subprocess injection."""
        return {name: cred.value for name, cred in self.resolved.items()}

    def summary(self) -> str:
        """Human-readable summary of resolution results."""
        parts = [f"✓ {len(self.resolved)} resolved"]
        if self.missing:
            parts.append(f"✗ {len(self.missing)} missing: {', '.join(self.missing)}")
        if self.errors:
            parts.append(f"⚠ {len(self.errors)} errors: {', '.join(self.errors.keys())}")
        return ", ".join(parts)


class CredentialProvider(ABC):
    """
    Abstract base for credential resolution.

    Implementations resolve secret values from different backends.
    The PA calls resolve() at boot and before dispatch.
    """

    name: str = "base"

    @abstractmethod
    async def get(self, spec: CredentialSpec) -> str | None:
        """
        Attempt to resolve a single credential.

        Returns the secret value, or None if this provider can't resolve it.
        """

    async def resolve(self, manifest: CredentialManifest) -> ResolutionResult:
        """
        Resolve all credentials in a manifest.

        For each credential, attempts to get the value. Tracks what was
        resolved, what's missing, and any errors.
        """
        result = ResolutionResult()

        for spec in manifest.credentials:
            try:
                value = await self.get(spec)
                if value is not None:
                    result.resolved[spec.name] = ResolvedCredential(
                        name=spec.name,
                        value=value,
                        source=self.name,
                    )
                elif spec.required:
                    result.missing.append(spec.name)
            except Exception as e:
                result.errors[spec.name] = str(e)
                if spec.required:
                    result.missing.append(spec.name)

        return result

    async def resolve_for_role(
        self, manifest: CredentialManifest, role: str
    ) -> ResolutionResult:
        """Resolve only the credentials needed by a specific role."""
        role_manifest = CredentialManifest(credentials=manifest.for_role(role))
        return await self.resolve(role_manifest)


# ---------------------------------------------------------------------------
# Concrete Providers
# ---------------------------------------------------------------------------


class EnvCredentialProvider(CredentialProvider):
    """
    Resolves credentials from environment variables.

    This is the default and simplest provider. It looks up the credential
    name (or env_var override) in os.environ.
    """

    name = "env"

    async def get(self, spec: CredentialSpec) -> str | None:
        return os.environ.get(spec.lookup_key)


class StaticCredentialProvider(CredentialProvider):
    """
    Resolves credentials from an in-memory dict.

    Useful for testing, CI injection, or programmatic setup.
    """

    name = "static"

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def get(self, spec: CredentialSpec) -> str | None:
        return self._credentials.get(spec.name) or self._credentials.get(spec.lookup_key)


class ChainCredentialProvider(CredentialProvider):
    """
    Tries multiple providers in order, returns first match.

    This is how you compose providers:
        chain = ChainCredentialProvider([
            SopsProvider(".pm/secrets.yaml.age"),  # Try encrypted file first
            EnvProvider(),                          # Fall back to env vars
        ])

    The chain short-circuits on first resolution per credential.
    """

    name = "chain"

    def __init__(self, providers: list[CredentialProvider]) -> None:
        self.providers = providers

    async def get(self, spec: CredentialSpec) -> str | None:
        for provider in self.providers:
            value = await provider.get(spec)
            if value is not None:
                logger.debug(
                    "Credential '%s' resolved by %s provider",
                    spec.name,
                    provider.name,
                )
                return value
        return None

    async def resolve(self, manifest: CredentialManifest) -> ResolutionResult:
        """
        Resolve using the chain. Each credential tries all providers in order.
        Tracks which provider resolved each credential.
        """
        result = ResolutionResult()

        for spec in manifest.credentials:
            resolved = False
            for provider in self.providers:
                try:
                    value = await provider.get(spec)
                    if value is not None:
                        result.resolved[spec.name] = ResolvedCredential(
                            name=spec.name,
                            value=value,
                            source=provider.name,
                        )
                        resolved = True
                        break
                except Exception as e:
                    result.errors[spec.name] = f"{provider.name}: {e}"

            if not resolved and spec.required:
                result.missing.append(spec.name)

        return result


# ---------------------------------------------------------------------------
# Boot-time validation
# ---------------------------------------------------------------------------


async def validate_credentials(
    manifest: CredentialManifest,
    provider: CredentialProvider,
    role: str | None = None,
) -> ResolutionResult:
    """
    Validate that all required credentials can be resolved.

    Called at PM boot time. Returns a ResolutionResult that the PM
    can check before accepting work.

    Args:
        manifest: The project's credential manifest
        provider: The configured provider (or chain)
        role: If set, only validate credentials for this role
    """
    if role:
        result = await provider.resolve_for_role(manifest, role)
    else:
        result = await provider.resolve(manifest)

    if result.ok:
        logger.info("Credentials validated: %s", result.summary())
    else:
        logger.warning("Credential validation failed: %s", result.summary())

    return result
