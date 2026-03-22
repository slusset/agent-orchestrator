---
name: project-planning
description: >-
  Manage the strategic roadmap and project plan. Use when reviewing project status,
  advancing milestones, adding capabilities, deciding what to work on next, or
  providing strategic context for planning. This skill manages the plan layer that
  sits above IDD specs and below vision. The plan is consumed by the PA for context —
  agents never see it.
argument-hint: "[status | advance <milestone-id> | next | add-capability <milestone-id> | link <capability-id>]"
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
---

# Project Planning

## Purpose

Manage the strategic roadmap (`plan/roadmap.yaml`) — the layer between vision and specs. This skill defines **when** and **in what order** work happens, not **what** the work is (that's IDD specs) or **how** to do it (that's agents).

## When to Use

- Starting a work session ("where are we?")
- Deciding what to work on next
- Advancing a milestone or capability after work is done
- Adding new capabilities discovered during development
- Linking capabilities to IDD specs or GitHub issues
- Providing strategic context before invoking IDD skills

## Core Rule: Never Edit plan/roadmap.yaml Directly

All mutations go through `PlanManager` tools. This enforces:
- Valid milestone lifecycle transitions
- Dependency satisfaction before advancement
- Capability uniqueness across milestones
- Referential integrity of all links

```python
from src.plan.manager import PlanManager

pm = PlanManager("plan/roadmap.yaml")
pm.load()

# CORRECT: validated mutation
pm.advance_milestone("m2-invocation", MilestoneStatus.IN_PROGRESS)
pm.advance_capability("callback-server", CapabilityStatus.COMPLETE)
pm.link_issue("callback-server", "#5")
pm.link_spec("callback-server", "specs/features/callback-server.feature")

# WRONG: raw YAML edit — will drift, break invariants, skip validation
```

## Workflow

### 1. Check Status (start of session)

```python
pm = PlanManager("plan/roadmap.yaml")
pm.load()
ctx = pm.planning_context()
# Returns: progress, active milestones, available milestones, blocked milestones
```

Or via CLI:
```bash
uv run python tools/validate-plan.py
```

### 2. Decide What's Next

```python
startable = pm.what_can_start()
# Returns capabilities whose milestone is active AND dependencies are met
```

Decision tree:
1. Are there active milestones with incomplete capabilities? → Work on those
2. Are there available milestones (deps satisfied, status=planned)? → Start one
3. Is everything blocked? → Address blockers or re-plan

### 3. Start Work

Before invoking IDD skills (solution-narrative, domain-modeling, behavior-contract):

```python
# Advance the capability you're about to work on
pm.advance_capability("callback-server", CapabilityStatus.IN_PROGRESS)
```

Then invoke the appropriate IDD skill for the work itself.

### 4. Complete Work

After implementation, testing, and validation:

```python
# Link artifacts created during the work
pm.link_spec("callback-server", "specs/features/callback-server.feature")
pm.link_issue("callback-server", "#5")

# Mark complete
pm.advance_capability("callback-server", CapabilityStatus.COMPLETE)

# If all capabilities in the milestone are done:
pm.advance_milestone("m2-invocation", MilestoneStatus.COMPLETE)
```

### 5. Discover New Work

If you discover a new capability during development:

```python
from src.plan.schema import Capability

pm.add_capability("m2-invocation", Capability(
    id="error-retry-logic",
    name="Error retry and circuit breaker for HTTP dispatch",
    description="Handle transient failures when dispatching to remote agents",
    depends_on=["agent-http-dispatch"],
))
```

## Role Rules

| Role | Can Do | Cannot Do |
|------|--------|-----------|
| **PA / PM** | Read roadmap, advance milestones, advance capabilities, add capabilities, link specs/issues | — |
| **Coding Agent** | Read roadmap (for context only) | Modify roadmap in any way |
| **UAT Agent** | Read roadmap (for context only) | Modify roadmap in any way |
| **DevOps Agent** | Read roadmap (for context only) | Modify roadmap in any way |
| **PR Agent** | Read roadmap (for IDD compliance checks) | Modify roadmap in any way |

The plan is the PA's strategic document. Agents receive TaskBundles derived from it.

## Milestone Lifecycle

```
planned → in_progress → complete (terminal)
   ↓           ↓
 deferred ← blocked
   ↓
 planned (re-plan)
```

**Guards:**
- `planned → in_progress`: All `depends_on` milestones must be `complete`
- `in_progress → complete`: All capabilities must be `complete`
- `complete → *`: Terminal state, no transitions allowed

## Capability Lifecycle

```
not_started → in_progress → complete
                  ↓
               deferred
```

**Guards:**
- `not_started → in_progress`: Parent milestone must be `in_progress`
- `in_progress → complete`: All `depends_on` capabilities must be `complete`

## Relationship to IDD Skills

This skill operates ABOVE the IDD chain:

```
project-planning (this skill)
    "We should build callback-server next, it's in m2-invocation"
        ↓
solution-narrative
    "Here's the user journey for callback handling"
        ↓
domain-modeling
    "Here are the entities: CallbackEvent, TaskResult"
        ↓
behavior-contract
    "Here's the Gherkin + AsyncAPI contract for callbacks"
        ↓
implementation
    "Here's the code"
        ↓
certification
    "Here's the evidence chain"
```

After each IDD skill produces artifacts, come back to this skill to `link_spec()` and advance capability status.

## CI Validation

```bash
uv run python tools/validate-plan.py            # Schema + structural validation
uv run python tools/validate-plan.py --strict    # Also verify spec_refs exist on disk
```

This runs in CI to catch any drift that bypasses PlanManager.

## Guardrails

- One roadmap per project (`plan/roadmap.yaml`)
- Milestone IDs are kebab-case, prefixed with `m{N}-` for ordering clarity
- Capability IDs are kebab-case, globally unique across all milestones
- Never skip milestones — dependencies exist for a reason
- If a capability is discovered during work, add it to the appropriate milestone before working on it
- Always link specs and issues to capabilities for traceability
