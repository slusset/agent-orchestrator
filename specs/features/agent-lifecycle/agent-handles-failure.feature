# id: agent-handles-failure
# type: feature
# story: specs/stories/agent-lifecycle/agent-handles-failure.md
# journey: specs/journeys/agent-execution-lifecycle.md
# model: specs/models/task/task.lifecycle.yaml

@agent-lifecycle
Feature: Agent handles failures gracefully
  As a Coding Agent
  I want to handle failures at each phase and report them clearly
  So that the PA can make informed decisions about retrying or escalating

  Background:
    Given a coding agent has received a valid CodingBundle
    And the agent has a StatusReporter configured

  @failure @workspace
  Scenario: Workspace setup fails due to bad repo URL
    Given the CodingBundle has an invalid repo_url
    When the agent attempts to clone the repository
    Then the agent reports failure immediately via callback
    And the TaskResult includes error "Failed to clone repository"
    And the task transitions to "failed"

  @failure @workspace
  Scenario: Workspace setup fails due to auth issues
    Given the agent lacks credentials for the repository
    When the agent attempts to clone the repository
    Then the agent reports failure with error "Authentication failed"
    And the error includes guidance on required credentials

  @failure @implementation
  Scenario: Implementation fails with actionable error
    Given the agent is implementing changes
    When the implementation encounters an error
    Then the agent reports failure with what was attempted
    And the TaskResult includes the specific error message
    And the error is actionable, not a raw stack trace

  @failure @tests @retry
  Scenario: Test failure triggers one retry attempt
    Given the agent has completed implementation
    When the agent runs tests and 3 tests fail
    Then the agent attempts to fix the failures using LLM
    And the agent runs the tests again

  @failure @tests
  Scenario: Tests pass after fix attempt
    Given the agent's first test run had failures
    When the agent applies fixes and reruns tests
    And all tests pass on the second run
    Then the agent proceeds to create the PR
    And the result metadata notes that a fix attempt was made

  @failure @tests
  Scenario: Tests still fail after fix attempt
    Given the agent's first test run had failures
    And the agent applied fixes
    When the agent reruns tests and they still fail
    Then the agent reports failure with the test output
    And the TaskResult includes the failing test names
    And the TaskResult includes the fix attempt details

  @failure @context
  Scenario: All failures include context for PA decision-making
    Given any agent failure occurs
    When the agent reports the failure
    Then the TaskResult includes a human-readable summary
    And the summary describes what phase the failure occurred in
    And the errors list contains specific, actionable messages
    And the PA can determine whether to retry or escalate

  @cleanup
  Scenario: Agent cleans up workspace on failure
    Given the agent created a feature branch
    When the agent fails during implementation
    Then the agent deletes the feature branch
    And the agent cleans up any temporary files
