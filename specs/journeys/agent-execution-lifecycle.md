---
id: agent-execution-lifecycle
type: journey
refs:
  persona: specs/personas/coding-agent.md
---

# Journey: Agent Execution Lifecycle

## Actor
Coding Agent (representative of any specialized agent).
Source Persona: specs/personas/coding-agent.md

## Trigger
Agent receives a TaskBundle from the PA.

## Preconditions
- Agent is registered and available
- TaskBundle is valid and complete
- callback_url in the bundle is reachable
- Agent has access to the repository (for Coding/UAT agents)

## Flow

### 1. Receive TaskBundle
- **User intent**: Agent receives work assignment
- **System response**: Agent validates the bundle, extracts objective, acceptance criteria, and configuration
- **Next**: Initialize Workspace

### 2. Initialize Workspace
- **User intent**: Agent sets up the environment for the task
- **System response**: Clone repo, create branch (Coding Agent), set up test environment (UAT Agent), configure providers (DevOps Agent)
- **Next**: Begin Work

### 3. Begin Work
- **User intent**: Agent starts the heartbeat loop and begins executing
- **System response**: HeartbeatRunner starts sending periodic status updates to callback_url. Agent begins domain-specific work.
- **Next**: Execute (iterative)

### 4. Execute (iterative)
- **User intent**: Agent does its work in phases, reporting progress
- **System response**: Agent sends heartbeats with progress_pct and descriptive messages. For Coding Agent: analyze → implement → test. For UAT: setup → run scenarios → report. For DevOps: configure → deploy → health check.
- **Next**: Report Result

### 5. Handle Blockers
- **User intent**: Agent encounters something it can't resolve
- **System response**: Agent sends a BLOCKED status update with the reason. PA receives and decides: wait, provide more context, or reassign.
- **Next**: Resume Work (if unblocked) or Fail (if unresolvable)

### 6. Report Result
- **User intent**: Agent signals completion
- **System response**: Agent sends TaskResult via callback with success/failure, summary, and artifacts (PR URL, deployment URL, test report, etc.)
- **Next**: (Agent's work is done — PA takes over)

## Outcomes
- **Success**: TaskResult sent with success=true and relevant artifacts
- **Failure modes**:
  - Invalid TaskBundle → immediate failure, report to PA
  - Workspace setup fails → report failure with diagnostics
  - Work fails after retries → report failure with errors
  - Callback unreachable → log locally, retry with backoff
  - Timeout → PA detects via missing heartbeats

## Related Stories
- specs/stories/agent-lifecycle/agent-receives-bundle.md
- specs/stories/agent-lifecycle/agent-reports-progress.md
- specs/stories/agent-lifecycle/agent-handles-failure.md

## E2E Coverage
- (to be created)
