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
  Story: specs/stories/orchestration/pa-resolves-credentials.md
  Feature: specs/features/orchestration/pa-resolves-credentials.feature
  Domain Model: specs/models/project/credentials.model.yaml
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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


def _parse_dotenv(content: str) -> dict[str, str]:
    """
    Parse .env file content into a dict.

    Handles:
    - KEY=value
    - KEY="quoted value" and KEY='quoted value'
    - Comments (lines starting with #)
    - Blank lines
    - export KEY=value (bash-style)
    - Inline comments after unquoted values

    Does NOT handle multiline values or variable interpolation.
    """
    result: dict[str, str] = {}

    for line in content.splitlines():
        line = line.strip()

        # Skip blanks and comments
        if not line or line.startswith("#"):
            continue

        # Strip optional 'export ' prefix
        if line.startswith("export "):
            line = line[7:].strip()

        # Must have an = sign
        if "=" not in line:
            continue

        key, _, raw_value = line.partition("=")
        key = key.strip()
        raw_value = raw_value.strip()

        if not key:
            continue

        # Handle quoted values
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ('"', "'"):
            value = raw_value[1:-1]
        else:
            # Bare value starting with # is an inline comment (value is empty)
            if raw_value.startswith("#"):
                value = ""
            elif " #" in raw_value:
                # Strip inline comments for unquoted values
                value = raw_value[: raw_value.index(" #")].strip()
            else:
                value = raw_value

        result[key] = value

    return result


class DotEnvCredentialProvider(CredentialProvider):
    """
    Resolves credentials from a .env file.

    Reads key=value pairs from a dotenv file on disk. Useful for local
    development where secrets live in .env (gitignored) rather than
    being exported into the shell environment.

    Usage:
        provider = DotEnvCredentialProvider(".env")
        # or in a chain:
        chain = ChainCredentialProvider([
            DotEnvCredentialProvider(".env"),
            EnvCredentialProvider(),
        ])
    """

    name = "dotenv"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        """Load and cache the .env file contents."""
        if self._cache is None:
            if not self._path.exists():
                logger.debug("DotEnv file not found: %s", self._path)
                self._cache = {}
            else:
                content = self._path.read_text(encoding="utf-8")
                self._cache = _parse_dotenv(content)
                logger.debug(
                    "Loaded %d entries from %s", len(self._cache), self._path
                )
        return self._cache

    def reload(self) -> None:
        """Clear the cache so the next get() re-reads from disk."""
        self._cache = None

    async def get(self, spec: CredentialSpec) -> str | None:
        entries = self._load()
        return entries.get(spec.lookup_key)


class SopsCredentialProvider(CredentialProvider):
    """
    Resolves credentials from a SOPS-encrypted file.

    Calls `sops --decrypt <path>` at load time, parses the decrypted
    output as dotenv (KEY=value) format, and serves values from the
    in-memory cache.

    Supported encrypted formats:
    - .env (dotenv format, decrypts to KEY=value lines)
    - .yaml / .json (decrypts to structured data, parsed accordingly)

    Requires `sops` CLI on PATH and the appropriate key (age, GPG, etc.)
    configured in .sops.yaml or SOPS_AGE_KEY_FILE env var.

    Usage:
        provider = SopsCredentialProvider(".pm/secrets.env")
        # or in a chain:
        chain = ChainCredentialProvider([
            SopsCredentialProvider(".pm/secrets.env"),
            EnvCredentialProvider(),  # fallback
        ])
    """

    name = "sops"

    def __init__(self, path: str | Path, sops_binary: str = "sops") -> None:
        self._path = Path(path)
        self._sops_binary = sops_binary
        self._cache: dict[str, str] | None = None

    async def _decrypt(self) -> str:
        """Run sops --decrypt and return the plaintext output."""
        if not self._path.exists():
            raise FileNotFoundError(f"SOPS encrypted file not found: {self._path}")

        if not shutil.which(self._sops_binary):
            raise RuntimeError(
                f"'{self._sops_binary}' not found on PATH. "
                "Install SOPS: https://github.com/getsops/sops"
            )

        proc = await asyncio.create_subprocess_exec(
            self._sops_binary, "--decrypt", str(self._path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"sops --decrypt failed (exit {proc.returncode}): {error_msg}"
            )

        return stdout.decode("utf-8")

    def _parse_output(self, plaintext: str) -> dict[str, str]:
        """Parse decrypted output based on file extension."""
        suffix = self._path.suffix.lower()

        if suffix in (".yaml", ".yml"):
            import yaml

            data = yaml.safe_load(plaintext) or {}
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict from SOPS YAML, got {type(data).__name__}")
            # Flatten to string values (single-level only)
            return {k: str(v) for k, v in data.items() if v is not None}

        elif suffix == ".json":
            import json

            data = json.loads(plaintext)
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict from SOPS JSON, got {type(data).__name__}")
            return {k: str(v) for k, v in data.items() if v is not None}

        else:
            # Default: dotenv format (.env, .txt, or anything else)
            return _parse_dotenv(plaintext)

    async def _load(self) -> dict[str, str]:
        """Decrypt and cache the file contents."""
        if self._cache is None:
            plaintext = await self._decrypt()
            self._cache = self._parse_output(plaintext)
            logger.debug(
                "Decrypted %d entries from %s via SOPS",
                len(self._cache),
                self._path,
            )
        return self._cache

    async def reload(self) -> None:
        """Clear cache so next get() re-decrypts from disk."""
        self._cache = None

    async def get(self, spec: CredentialSpec) -> str | None:
        entries = await self._load()
        return entries.get(spec.lookup_key)


class ChainCredentialProvider(CredentialProvider):
    """
    Tries multiple providers in order, returns first match.

    This is how you compose providers:
        chain = ChainCredentialProvider([
            SopsCredentialProvider(".pm/secrets.env"),  # Try encrypted first
            DotEnvCredentialProvider(".env"),            # Then local .env
            EnvCredentialProvider(),                     # Fall back to env vars
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


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_credential_manifest(path: str | os.PathLike[str]) -> CredentialManifest:
    """
    Load a CredentialManifest from a YAML file.

    Expected format:
        credentials:
          - name: ANTHROPIC_API_KEY
            required: true
            source: env
            ...

    Args:
        path: Path to credentials.yaml (typically .pm/credentials.yaml)

    Returns:
        Parsed CredentialManifest

    Raises:
        FileNotFoundError: If the manifest file doesn't exist
        ValueError: If the YAML is malformed
    """
    import yaml
    from pathlib import Path

    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Credential manifest not found: {manifest_path}")

    with manifest_path.open() as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict) or "credentials" not in data:
        raise ValueError(
            f"Invalid credential manifest at {manifest_path}: "
            "expected a 'credentials' key"
        )

    return CredentialManifest(credentials=[
        CredentialSpec(**entry) for entry in data["credentials"]
    ])
