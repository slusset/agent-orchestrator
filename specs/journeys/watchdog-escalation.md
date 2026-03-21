---
id: watchdog-escalation
type: journey
refs:
  persona: specs/personas/primary-agent.md
---

# Journey: Watchdog and Escalation

## Actor
Primary Agent — monitoring dispatched agents for health.
Source Persona: specs/personas/primary-agent.md

## Trigger
PA's watchdog loop detects a missed heartbeat or timeout threshold crossed.

## Preconditions
- At least one task is dispatched and in progress
- Heartbeat interval and timeout thresholds are configured in the TaskBundle
- PA has the agent's task_id and expected reporting schedule

## Flow

### 1. Monitor Heartbeats
- **User intent**: PA tracks when each agent last reported
- **System response**: PA compares last_heartbeat timestamp against status_interval. If within tolerance, no action.
- **Next**: Check Timeout (continuous loop)

### 2. Detect Missed Heartbeat
- **User intent**: PA notices an agent hasn't reported within expected interval
- **System response**: PA logs a warning. If within soft threshold (2x interval), wait. If beyond soft threshold, nudge.
- **Next**: Nudge Agent or Escalate

### 3. Nudge Agent
- **User intent**: PA checks if the agent is still alive
- **System response**: PA sends a health check to the agent (if A2A) or checks the process (if subprocess). If agent responds, reset timer.
- **Next**: Resume Monitoring or Escalate

### 4. Escalate — Agent Unresponsive
- **User intent**: PA determines the agent is stuck or dead
- **System response**: PA cancels the agent's task, records failure in state, decides on next action: retry with same agent, reassign to different agent, or notify stakeholder.
- **Next**: Retry or Notify Stakeholder

### 5. Notify Stakeholder
- **User intent**: PA informs stakeholder that a task is stuck
- **System response**: Stakeholder receives: task description, how long it's been stuck, what was tried, options for resolution
- **Next**: Stakeholder Decides

## Outcomes
- **Success**: Agent resumes after nudge, or task is successfully reassigned
- **Failure modes**:
  - Agent process crashed → detected via heartbeat gap, task reassigned
  - Agent stuck in loop → timeout triggers cancellation
  - All retry attempts fail → stakeholder notified for manual intervention

## Related Stories
- specs/stories/monitoring/pa-detects-timeout.md
- specs/stories/monitoring/pa-escalates-to-stakeholder.md

## E2E Coverage
- (to be created)
