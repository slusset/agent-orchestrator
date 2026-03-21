# id: agent-reports-progress
# type: feature
# story: specs/stories/agent-lifecycle/agent-reports-progress.md
# journey: specs/journeys/agent-execution-lifecycle.md
# model: specs/models/shared/status-update.model.yaml

@agent-lifecycle
Feature: Agent reports progress and completion
  As a Coding Agent
  I want to send heartbeats while working and a final result when done
  So that the PA knows I'm alive and can process my deliverables

  Background:
    Given a coding agent is executing a task
    And the task has status_interval of 300 seconds
    And the agent has a StatusReporter configured

  @happy-path
  Scenario: Agent sends periodic heartbeats
    Given the agent is working on implementation
    When 300 seconds elapse
    Then the agent sends a heartbeat via the StatusReporter
    And the heartbeat includes status "in_progress"
    And the heartbeat includes a descriptive message
    And the heartbeat includes a timestamp

  @happy-path
  Scenario: Agent sends manual progress updates between phases
    Given the agent transitions from "analyzing" to "implementing"
    When the agent calls reporter.heartbeat with progress_pct 30
    Then a StatusUpdate is sent with progress_pct 30
    And the message describes the current phase

  @happy-path
  Scenario: Agent reports successful completion
    Given the agent has finished implementation and tests pass
    And the agent has created PR "https://github.com/org/app/pull/42"
    When the agent completes
    Then a TaskResult is sent with success true
    And the result includes a summary of what was done
    And the result artifacts include the PR URL
    And the result metadata includes files_changed and test_count

  @blocked
  Scenario: Agent reports blocked status
    Given the agent encounters a dependency it cannot resolve
    When the agent calls reporter.blocked with reason "Missing API key for external service"
    Then a StatusUpdate is sent with status "blocked"
    And the message describes what the agent is blocked on

  @failure
  Scenario: Agent reports failure with error details
    Given the agent encounters an unrecoverable error
    When the agent fails
    Then a TaskResult is sent with success false
    And the result includes error messages
    And the errors are actionable, not just stack traces

  @resilience
  Scenario: Agent retries callback on network failure
    Given the callback_url is temporarily unreachable
    When the agent attempts to send a heartbeat
    Then the agent retries with exponential backoff
    And the agent continues working while retrying
    And the agent logs the retry attempts

  @concurrency
  Scenario: Heartbeats run in background without blocking work
    Given the HeartbeatRunner is active
    When the agent is performing a long-running operation
    Then heartbeats continue to fire at the configured interval
    And the main work thread is not blocked by heartbeat sends
