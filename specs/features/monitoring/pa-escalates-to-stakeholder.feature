# id: pa-escalates-to-stakeholder
# type: feature
# story: specs/stories/monitoring/pa-escalates-to-stakeholder.md
# journey: specs/journeys/watchdog-escalation.md
# model: specs/models/story/story.lifecycle.yaml

@monitoring
Feature: PA escalates to stakeholder
  As the Primary Agent
  I want to notify the stakeholder when a task is stuck and I can't resolve it
  So that they can make a decision about how to proceed

  Background:
    Given the orchestrator is running
    And a task has failed after exhausting retry attempts
    And the stakeholder is reachable via the configured notification channel

  @happy-path
  Scenario: PA sends escalation with full context
    Given a coding task failed with error "Tests failed after fix attempt"
    And the task has been retried 2 times
    When the PA escalates to the stakeholder
    Then the notification includes the task description
    And the notification includes how long the task has been stuck
    And the notification includes what was tried (2 retries)
    And the notification includes the error details
    And the notification includes options: retry, reassign, cancel, provide guidance

  @decision @retry
  Scenario: Stakeholder decides to retry
    Given the PA has escalated a failed task
    When the stakeholder responds with "retry"
    Then the PA creates a new task with the same bundle
    And the retry count is reset
    And the story remains in its current status

  @decision @cancel
  Scenario: Stakeholder decides to cancel
    Given the PA has escalated a failed task
    When the stakeholder responds with "cancel"
    Then the PA cancels the task
    And the story transitions to "cancelled"

  @decision @guidance
  Scenario: Stakeholder provides guidance
    Given the PA has escalated a failed task
    When the stakeholder responds with additional context
    Then the PA creates a new task with the updated context
    And the new bundle includes the stakeholder's guidance

  @timeout
  Scenario: PA takes default action when stakeholder doesn't respond
    Given the PA has escalated a failed task
    And the stakeholder has not responded within the escalation threshold
    When the escalation timeout is reached
    Then the PA cancels the task
    And the PA creates a JIRA ticket documenting the failure
    And the story transitions to "failed"

  @edge-case
  Scenario: Escalation is a last resort after exhausting retries
    Given a task has failed
    And retry_count is less than max_retries
    When the PA evaluates the failure
    Then the PA retries before escalating
    And escalation only happens after all retries are exhausted
