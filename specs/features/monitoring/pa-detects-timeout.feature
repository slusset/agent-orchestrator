# id: pa-detects-timeout
# type: feature
# story: specs/stories/monitoring/pa-detects-timeout.md
# journey: specs/journeys/watchdog-escalation.md
# model: specs/models/task/task.model.yaml

@monitoring
Feature: PA detects agent timeout
  As the Primary Agent
  I want to detect when an agent stops responding and take corrective action
  So that work doesn't silently stall

  Background:
    Given the orchestrator is running with watchdog enabled
    And a task is dispatched with status_interval 300 seconds
    And the task has timeout_minutes 60

  @happy-path
  Scenario: PA tracks heartbeat timestamps
    Given the agent sends a heartbeat
    When the PA receives the StatusUpdate
    Then the task's last_heartbeat is updated to the current timestamp
    And the watchdog timer is reset

  @detection @soft-threshold
  Scenario: PA detects missed heartbeat at soft threshold
    Given the agent's last heartbeat was 600 seconds ago
    And the soft threshold is 2x status_interval (600 seconds)
    When the watchdog checks the task
    Then the PA logs a warning about the missed heartbeat
    And the PA does not yet cancel the task

  @detection @nudge
  Scenario: PA nudges an unresponsive agent
    Given the agent has exceeded the soft threshold
    When the PA sends a health check to the agent
    And the agent responds to the health check
    Then the PA resets the timeout timer
    And the task remains in "in_progress" status

  @detection @nudge-failed
  Scenario: PA nudge fails - agent is unreachable
    Given the agent has exceeded the soft threshold
    When the PA sends a health check to the agent
    And the agent does not respond
    Then the PA increments the missed-nudge counter
    And the PA waits for the hard threshold before cancelling

  @detection @hard-threshold
  Scenario: PA cancels task at hard timeout threshold
    Given the agent has not responded for 60 minutes
    And the hard threshold (timeout_minutes) has been reached
    When the watchdog checks the task
    Then the PA cancels the task
    And the task transitions to "failed"
    And the failure reason is "timeout: no heartbeat for 60 minutes"

  @retry
  Scenario: PA retries timed-out task if retries remain
    Given a task timed out
    And the task's retry_count is 0 and max_retries is 2
    When the PA evaluates the timeout failure
    Then the PA creates a new task with the same bundle
    And the new task's retry_count is 1
    And the new task's parent_task_id references the timed-out task

  @escalation
  Scenario: PA escalates after exhausting retries on timeout
    Given a task timed out
    And the task's retry_count equals max_retries
    When the PA evaluates the timeout failure
    Then the PA escalates to the stakeholder
