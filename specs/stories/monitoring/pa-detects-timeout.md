---
id: pa-detects-timeout
type: story
refs:
  journey: specs/journeys/watchdog-escalation.md
  persona: specs/personas/primary-agent.md
  steps: [1, 2, 3]
---

# Story: PA Detects Agent Timeout

## Narrative
As the Primary Agent,
I want to detect when an agent stops responding and take corrective action,
So that work doesn't silently stall.

## Acceptance Criteria
- [ ] PA tracks last_heartbeat timestamp for each dispatched task
- [ ] PA detects when an agent exceeds its status_interval by a soft threshold (2x)
- [ ] PA attempts to nudge the agent (health check) before declaring it dead
- [ ] If agent responds to nudge, PA resets the timeout timer
- [ ] If agent doesn't respond within hard threshold, PA cancels the task
- [ ] PA records the timeout in the task's status (FAILED with timeout reason)
- [ ] PA evaluates whether to retry or escalate

## Notes
- The watchdog runs as a separate loop, not as a graph node
- Soft threshold = 2x status_interval, hard threshold = timeout_minutes from TaskBundle
- Nudge mechanism depends on invocation pattern (HTTP health check, process signal, etc.)
