---
id: agent-reports-progress
type: story
refs:
  journey: specs/journeys/agent-execution-lifecycle.md
  persona: specs/personas/coding-agent.md
  steps: [3, 4, 6]
---

# Story: Agent Reports Progress and Completion

## Narrative
As a Coding Agent,
I want to send heartbeats while working and a final result when done,
So that the PA knows I'm alive and can process my deliverables.

## Acceptance Criteria
- [ ] Agent sends periodic heartbeats at the configured status_interval
- [ ] Heartbeats include a descriptive message and optional progress_pct
- [ ] Agent can report BLOCKED status with a reason
- [ ] On success, agent sends a TaskResult with summary and artifacts (PR URL, etc.)
- [ ] On failure, agent sends a TaskResult with success=false and error details
- [ ] If callback is unreachable, agent retries with exponential backoff
- [ ] Heartbeats run in the background and don't block the agent's main work

## Notes
- The HeartbeatRunner handles automatic background heartbeats
- The agent calls reporter.heartbeat() for manual progress updates between phases
- The transport (HTTP, websocket, queue) is abstracted by StatusReporter
