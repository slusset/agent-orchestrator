"""
DevOpsBundle: TaskBundle specialized for the DevOps Agent.

Handles deployments across environments, secret management,
and coordination across infrastructure components (Vercel, Railway, Supabase).
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .task_bundle import TaskBundle


class DeployEnvironment(str, Enum):
    DEV = "dev"
    UAT = "uat"
    STAGING = "staging"
    PRODUCTION = "production"


class InfraProvider(str, Enum):
    VERCEL = "vercel"
    RAILWAY = "railway"
    SUPABASE = "supabase"


class DeployAction(str, Enum):
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    PROMOTE = "promote"  # Promote from one env to next
    TROUBLESHOOT = "troubleshoot"


class DevOpsBundle(TaskBundle):
    """
    Hand-off contract for the DevOps Agent.

    Manages deployments, troubleshooting, and infrastructure coordination.
    The agent handles secrets, environment variables, and multi-provider
    orchestration.
    """

    # What to do
    action: DeployAction = DeployAction.DEPLOY
    target_environment: DeployEnvironment
    providers: list[InfraProvider] = Field(default_factory=list)

    # Source
    repo_url: str
    branch: str = "main"
    commit_sha: str | None = None  # Pin to specific commit

    # Deployment config
    env_vars: dict[str, str] = Field(default_factory=dict)  # Non-secret env vars
    secret_refs: list[str] = Field(default_factory=list)  # References to secrets (not values!)

    # Workflow
    run_migrations: bool = False
    health_check_url: str | None = None
    rollback_on_failure: bool = True
    requires_approval: bool = Field(
        default=False,
        description="If True, agent pauses before final deploy and notifies PA",
    )

    # Troubleshooting context (when action=TROUBLESHOOT)
    error_description: str | None = None
    log_urls: list[str] = Field(default_factory=list)
