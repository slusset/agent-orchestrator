# Agent Orchestrator — CLAUDE.md

## Project Overview

Multi-agent orchestrator where a Primary Agent (PM) coordinates Coding, UAT, DevOps, and PR agents through the full software delivery lifecycle. Built with Intent-Driven Development (IDD) methodology from the ground up.

**Architecture**: The PA is a LangGraph StateGraph with `interrupt()` pattern. It dispatches TaskBundles to agents and pauses, resuming only when agents report back via callbacks.

## Build & Run

```bash
uv sync                              # Install all dependencies
uv run pytest                        # Run all tests
uv run pytest tests/plan/            # Run plan layer tests only
uv run python tools/validate-plan.py # Validate roadmap schema
uv run python tools/validate-plan.py --strict  # Verify spec_refs exist
```

## Project Structure

```
src/
  contracts/         # Transport-agnostic hand-off contracts (TaskBundle, StatusUpdate, etc.)
  orchestrator/      # PA graph, state, dispatcher, callback handler, server
  agents/            # Agent implementations (base, coding_agent, runner)
  plan/              # Strategic planning layer (schema, manager)
plan/
  roadmap.yaml       # THE roadmap — never edit directly, use PlanManager
specs/
  personas/          # IDD personas
  journeys/          # IDD user journeys
  stories/           # IDD user stories
  features/          # BDD feature files (Gherkin)
  models/            # Domain models (aggregates, value objects)
  capabilities/      # Capability specs
tests/
  plan/              # Plan schema and PlanManager tests
  contracts/         # TaskBundle, capability profile tests
  orchestrator/      # Dispatcher, graph tests
  agents/            # Agent implementation tests
tools/
  validate-plan.py   # CI validation for roadmap schema
```

## Key Architectural Patterns

### TaskBundle Contract
The fundamental unit of PA→Agent communication. Transport-agnostic (HTTP, queue, local). Every agent receives a TaskBundle with everything it needs to work independently.

### Interrupt Pattern
The graph dispatches a task and calls `interrupt()`. LangGraph checkpoints state and stops. When the agent's callback arrives, `Command(resume=result)` resumes the graph.

### Capability Profiles
The PA maintains `AgentCapabilityProfile` for each agent type. **Implicit skills** (git, pytest) are omitted from bundles. **Configurable skills** (IDD, BDD) must be explicitly included.

### Plan Layer
Strategic roadmap sits above IDD specs. **Never edit `plan/roadmap.yaml` directly.** All mutations go through `PlanManager` which validates invariants.

```python
from src.plan.manager import PlanManager
pm = PlanManager("plan/roadmap.yaml")
pm.load()
pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
```

## IDD (Intent-Driven Development)

This project follows IDD methodology. The traceability chain:

```
Persona → Journey → Story → Capability → Model → Feature → Contract → Code → Test → Evidence
```

### IDD Artifact Locations
- **Personas**: `specs/personas/`
- **Journeys**: `specs/journeys/` — user journey narratives
- **Stories**: `specs/stories/` — user stories derived from journeys
- **Features**: `specs/features/` — Gherkin BDD feature files
- **Models**: `specs/models/` — domain aggregates, entities, value objects
- **Capabilities**: `specs/capabilities/` — capability definitions

### Available IDD Skills

These skills are available for Claude Code sessions:

| Skill | Purpose | When to Use |
|-------|---------|-------------|
| `/solution-narrative` | Persona, journey, and story development | Capturing requirements, mapping user experiences |
| `/domain-modeling` | Entity, aggregate, value object definition | Translating stories into formal domain models |
| `/behavior-contract` | BDD features and protocol contracts | Converting stories into testable specs |
| `/certification` | Traceability verification and evidence | After implementation, before merge |
| `/pr-review` | IDD compliance on PRs | CI/CD or manual pre-merge checks |
| `/e2e-journey-testing` | End-to-end tests from journeys | Creating/updating e2e tests |
| `/project-planning` | Roadmap and milestone management | Strategic planning, status checks, work prioritization |

### IDD Workflow Layers

1. **Narrative** (`/solution-narrative`) — personas, journeys, stories
2. **Model** (`/domain-modeling`) — entities, aggregates, value objects
3. **Contract** (`/behavior-contract`) — Gherkin features, API contracts
4. **Implementation** — code that fulfills contracts
5. **Validation** — tests that verify implementation
6. **Certification** (`/certification`) — evidence chain from intent to proof

## Testing Requirements

### Traceability in Tests

All test files MUST include IDD traceability in their module docstrings:

```python
"""
Unit tests for [component].

Traceability:
  Journey: specs/journeys/[relevant-journey].md
  Story: specs/stories/[relevant-story].md
  Feature: specs/features/[relevant-feature].feature
  Domain Model: specs/models/[relevant-model].yaml
"""
```

Test classes and significant test methods should also reference specific features/scenarios where applicable.

### Test Categories

- **Unit tests** (`tests/`): Current focus. Test schema validation, state transitions, contract instantiation, skill filtering.
- **Behavior tests** (future): After BDD specs stabilize, driven by Gherkin feature files.
- **E2E journey tests** (future): Validate full user journeys end-to-end.

### Running Tests

```bash
uv run pytest                         # All tests
uv run pytest -v                      # Verbose output
uv run pytest tests/plan/             # Plan layer only
uv run pytest tests/contracts/        # Contracts only
uv run pytest tests/orchestrator/     # Orchestrator only
uv run pytest -k "test_milestone"     # Pattern match
```

## Role-Based Access

| Role | Can Modify | Read-Only |
|------|-----------|-----------|
| **PA / PM** | Roadmap, milestones, capabilities, links | Everything |
| **Coding Agent** | Code, tests | Roadmap (context only) |
| **UAT Agent** | Test results, evidence | Roadmap, specs |
| **DevOps Agent** | Infrastructure, deployments | Roadmap |
| **PR Agent** | PR reviews, merge decisions | Roadmap, specs |

The plan is the PA's strategic document. Agents receive TaskBundles derived from it — they never see or modify the roadmap.

## Roadmap Quick Reference

Current state of `plan/roadmap.yaml`:

| Milestone | Status | Key Capabilities |
|-----------|--------|-----------------|
| m1-foundation | **complete** | Contracts, graph, profiles, plan layer |
| m2-invocation | planned (ready) | Callback server, HTTP dispatch, GitHub webhooks |
| m3-coding-agent | planned (blocked by m2) | Git workspace, LLM implementation, test execution, PR creation |
| m4-lifecycle | planned (blocked by m3) | PR agent, UAT agent, DevOps agent, watchdog |
| m5-integrations | planned (blocked by m2) | JIRA, Confluence, GitHub events |

Check status: `uv run python tools/validate-plan.py`

## Development Workflow

1. **Check roadmap status** → `/project-planning status`
2. **Pick capability** → `pm.what_can_start()` or `/project-planning next`
3. **Start work** → Advance capability to in_progress via PlanManager
4. **Write specs first** → Use IDD skills (narrative → model → contract)
5. **Implement** → Code that fulfills the contracts
6. **Test** → Unit tests with IDD traceability docstrings
7. **Link artifacts** → `pm.link_spec()`, `pm.link_issue()`
8. **Complete** → Advance capability/milestone status
9. **Certify** → `/certification` to verify traceability chain

## CI Validation

```bash
uv run python tools/validate-plan.py            # Schema validation
uv run python tools/validate-plan.py --strict    # + verify spec_refs exist on disk
uv run pytest                                     # All unit tests
```

## Guardrails

- **Never edit `plan/roadmap.yaml` directly** — use PlanManager tools
- **Never skip milestones** — dependencies exist for a reason
- **TaskBundle is the contract** — agents must be able to work from it alone
- **Implicit skills are omitted** — don't over-instruct agents
- **Tests trace to IDD artifacts** — every test file references its journey/story/feature
- **Fix forward** — if specs drift from implementation, update specs, don't bypass them
