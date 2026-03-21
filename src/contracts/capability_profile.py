"""
AgentCapabilityProfile: What the PA knows about each agent's abilities.

The PA consults profiles during planning to decide:
1. WHICH agent to dispatch to
2. WHAT instructions to include in the TaskBundle
3. WHAT it can assume the agent already knows (implicit skills)
4. HOW to invoke the agent (local, HTTP, queue)

Profiles are the PA's mental model of its team — not the agents'
self-description. The PA maintains these based on registration
and observed performance.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InvocationMethod(str, Enum):
    """How the PA can invoke this agent."""

    LOCAL = "local"  # Same process, asyncio task
    HTTP = "http"  # JSON-RPC over HTTP
    QUEUE = "queue"  # Message queue (future)


class AgentCapabilityProfile(BaseModel):
    """
    The PA's knowledge of what an agent can do.

    Implicit skills: things the agent knows how to do without being told.
        The PA does NOT need to include instructions for these in the TaskBundle.
        Example: A coding agent implicitly knows git, Python, how to run pytest.

    Configurable skills: things the agent CAN do but needs explicit instructions.
        The PA MUST include these in the TaskBundle.skills list if needed.
        Example: "use Gherkin for specs", "follow this PR template".

    Tools: external tools the agent has access to.
        The PA uses this to know what's possible.
        Example: ["git", "gh_cli", "npm", "pytest", "docker"]
    """

    # Identity
    agent_type: str  # "coding", "uat", "devops", "pr"
    name: str  # Human-readable name
    description: str = ""

    # Skills
    implicit_skills: list[str] = Field(
        default_factory=list,
        description="Skills the agent has without being told. "
        "PA omits instructions for these.",
    )
    configurable_skills: list[str] = Field(
        default_factory=list,
        description="Skills the agent supports but needs explicit "
        "activation via TaskBundle.skills.",
    )

    # Tools
    tools: list[str] = Field(
        default_factory=list,
        description="External tools the agent can use.",
    )

    # Languages and frameworks
    supported_languages: list[str] = Field(default_factory=list)
    supported_frameworks: list[str] = Field(default_factory=list)

    # Invocation
    invocation: InvocationMethod = InvocationMethod.LOCAL
    endpoint: str | None = Field(
        None,
        description="HTTP endpoint for remote agents. "
        "Used when invocation=HTTP.",
    )

    # Constraints
    max_concurrent_tasks: int = 1
    max_timeout_minutes: int = 120

    # Metadata
    version: str = "0.1.0"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def has_skill(self, skill: str) -> bool:
        """Check if the agent has a skill (implicit or configurable)."""
        return skill in self.implicit_skills or skill in self.configurable_skills

    def has_implicit_skill(self, skill: str) -> bool:
        """Check if the agent knows this skill without being told."""
        return skill in self.implicit_skills

    def needs_explicit_skill(self, skill: str) -> bool:
        """Check if this skill needs to be listed in the TaskBundle."""
        return skill in self.configurable_skills and skill not in self.implicit_skills

    def has_tool(self, tool: str) -> bool:
        """Check if the agent has access to a tool."""
        return tool in self.tools

    def supports_language(self, language: str) -> bool:
        """Check if the agent can work with a language."""
        return language.lower() in [l.lower() for l in self.supported_languages]


# ---------------------------------------------------------------------------
# Default profiles — the PA's initial knowledge of its team
# ---------------------------------------------------------------------------

CODING_AGENT_PROFILE = AgentCapabilityProfile(
    agent_type="coding",
    name="Coding Agent",
    description="Implements features, writes tests, creates PRs. "
    "Works in isolation on a branch.",
    implicit_skills=[
        "git_operations",  # clone, branch, commit, push
        "code_generation",  # write code from specs
        "test_writing",  # write unit tests
        "code_review_self",  # self-review before PR
        "dependency_management",  # pip, npm, uv
    ],
    configurable_skills=[
        "bdd_specs",  # Write Gherkin feature files
        "tdd_workflow",  # Red-green-refactor cycle
        "idd_workflow",  # Intent-driven development compliance
        "docker_build",  # Build Dockerfiles
        "db_migrations",  # Write DB migrations
        "api_contracts",  # Generate OpenAPI/AsyncAPI from code
        "e2e_tests",  # Write end-to-end tests
    ],
    tools=["git", "gh_cli", "pytest", "npm", "uv", "docker"],
    supported_languages=["python", "typescript", "javascript"],
    supported_frameworks=["fastapi", "nextjs", "react", "langgraph"],
    invocation=InvocationMethod.LOCAL,
    max_concurrent_tasks=1,
    max_timeout_minutes=120,
)

UAT_AGENT_PROFILE = AgentCapabilityProfile(
    agent_type="uat",
    name="UAT Agent",
    description="Validates features against specs, user journeys, and "
    "acceptance criteria. Reviews repo state, not direct agent output.",
    implicit_skills=[
        "spec_reading",  # Parse Gherkin, user stories
        "test_execution",  # Run existing test suites
        "result_analysis",  # Analyze pass/fail results
    ],
    configurable_skills=[
        "exploratory_testing",  # Ad-hoc testing from specs
        "regression_testing",  # Validate nothing broke
        "accessibility_testing",  # A11y validation
        "performance_testing",  # Load/perf checks
        "visual_regression",  # Screenshot comparison
        "journey_validation",  # Test full user journeys
    ],
    tools=["playwright", "pytest", "lighthouse", "axe"],
    supported_languages=["python", "typescript"],
    supported_frameworks=["playwright", "cypress"],
    invocation=InvocationMethod.LOCAL,
    max_concurrent_tasks=2,
    max_timeout_minutes=60,
)

DEVOPS_AGENT_PROFILE = AgentCapabilityProfile(
    agent_type="devops",
    name="DevOps Agent",
    description="Deploys to environments, manages infrastructure, "
    "troubleshoots failures. Coordinates across providers.",
    implicit_skills=[
        "deploy_vercel",  # Vercel deployments
        "deploy_railway",  # Railway deployments
        "deploy_supabase",  # Supabase migrations/config
        "health_checks",  # Validate deployment health
        "log_analysis",  # Read and interpret logs
    ],
    configurable_skills=[
        "secret_rotation",  # Rotate secrets
        "rollback",  # Rollback deployments
        "infra_provisioning",  # Create new resources
        "github_actions",  # Manage CI/CD workflows
        "monitoring_setup",  # Configure alerting
    ],
    tools=["vercel_cli", "railway_cli", "supabase_cli", "gh_cli", "docker"],
    supported_languages=["python", "typescript", "yaml"],
    supported_frameworks=[],
    invocation=InvocationMethod.LOCAL,
    max_concurrent_tasks=1,
    max_timeout_minutes=30,
)

PR_AGENT_PROFILE = AgentCapabilityProfile(
    agent_type="pr",
    name="PR Agent",
    description="Reviews PRs, checks code quality, verifies acceptance "
    "criteria, and manages the merge process.",
    implicit_skills=[
        "code_review",  # Review code quality
        "diff_analysis",  # Understand code changes
        "test_verification",  # Verify tests pass
        "pr_management",  # Labels, reviewers, merge
    ],
    configurable_skills=[
        "idd_compliance",  # Check IDD traceability
        "security_review",  # Check for vulnerabilities
        "performance_review",  # Check for perf regressions
        "style_enforcement",  # Enforce coding standards
    ],
    tools=["gh_cli", "git"],
    supported_languages=["python", "typescript", "javascript"],
    supported_frameworks=[],
    invocation=InvocationMethod.LOCAL,
    max_concurrent_tasks=3,
    max_timeout_minutes=15,
)

# All default profiles
DEFAULT_PROFILES: dict[str, AgentCapabilityProfile] = {
    "coding": CODING_AGENT_PROFILE,
    "uat": UAT_AGENT_PROFILE,
    "devops": DEVOPS_AGENT_PROFILE,
    "pr": PR_AGENT_PROFILE,
}
