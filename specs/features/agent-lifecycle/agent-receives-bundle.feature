# id: agent-receives-bundle
# type: feature
# story: specs/stories/agent-lifecycle/agent-receives-bundle.md
# journey: specs/journeys/agent-execution-lifecycle.md
# model: specs/models/task/task.lifecycle.yaml

@agent-lifecycle
Feature: Agent receives and validates a TaskBundle
  As a Coding Agent
  I want to receive a TaskBundle with everything I need to work independently
  So that I can start executing without needing to ask the PA for clarification

  Background:
    Given a coding agent is running and ready to accept tasks

  @happy-path
  Scenario: Agent receives a valid CodingBundle
    Given a CodingBundle with objective "Add user authentication"
    And the bundle has a valid callback_url
    And the bundle has a valid repo_url
    And the bundle has acceptance criteria
    When the agent receives the bundle
    Then the agent validates all required fields
    And the agent extracts the objective and acceptance criteria
    And the agent initializes its workspace
    And the task transitions from "dispatched" to "in_progress"

  @happy-path
  Scenario: Agent creates a feature branch during workspace setup
    Given a valid CodingBundle targeting repo "git@github.com:org/app.git"
    And the bundle has branch_prefix "feature/"
    When the agent initializes its workspace
    Then the agent clones the repository
    And the agent creates a branch matching "feature/{task_id}-*"
    And the branch is based on the configured base_branch

  @happy-path
  Scenario: Agent respects scope boundaries
    Given a CodingBundle with focus_paths ["src/auth/"]
    And protected_paths [".github/", "infra/"]
    When the agent begins implementation
    Then the agent only modifies files within focus_paths
    And the agent does not touch files in protected_paths

  @validation
  Scenario: Agent rejects bundle with missing objective
    Given a CodingBundle without an objective
    When the agent receives the bundle
    Then the agent reports failure immediately via callback
    And the failure message indicates "missing required field: objective"

  @validation
  Scenario: Agent rejects bundle with unreachable callback
    Given a CodingBundle with callback_url "http://unreachable:9999/callback"
    When the agent attempts to validate the bundle
    Then the agent reports a validation error
    And the error indicates the callback_url is not reachable

  @decoupling
  Scenario: Agent does not need PA internal state or methodology
    Given a CodingBundle with concrete instructions
    When the agent processes the bundle
    Then the agent works from the bundle contents only
    And the agent makes no calls to the PA's state or checkpoint store
