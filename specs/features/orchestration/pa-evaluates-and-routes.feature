# id: pa-evaluates-and-routes
# type: feature
# story: specs/stories/orchestration/pa-evaluates-and-routes.md
# journey: specs/journeys/feature-request-lifecycle.md
# model: specs/models/story/story.lifecycle.yaml

@orchestration
Feature: PA evaluates results and routes to next agent
  As the Primary Agent
  I want to evaluate an agent's result and automatically route to the next step
  So that the story progresses through the full lifecycle without manual intervention

  Background:
    Given the orchestrator is running
    And a story exists in "in_progress" status
    And a coding task was dispatched and the graph is interrupted

  @happy-path
  Scenario: PA resumes graph on agent callback
    Given the coding agent sends a successful TaskResult
    And the result includes PR URL "https://github.com/org/app/pull/42"
    When the callback arrives at the PA
    Then the graph resumes from the interrupt point
    And the task status is updated to "completed"
    And the task's pr_url is set to the PR URL

  @routing @coding-to-pr
  Scenario: Coding success routes to PR review
    Given the coding task completed successfully with a PR
    When the PA evaluates the result
    Then the PA dispatches a PRBundle to the PR agent
    And the PRBundle references the PR URL from the coding result
    And the PRBundle includes the original acceptance criteria
    And the story transitions to "review"

  @routing @pr-to-uat
  Scenario: PR approval routes to UAT
    Given the PR agent approved and merged the PR
    When the PA evaluates the PR result
    Then the PA dispatches a UATBundle to the UAT agent
    And the UATBundle targets the main branch post-merge
    And the story transitions to "uat"

  @routing @uat-to-devops
  Scenario: UAT pass routes to staging deployment
    Given the UAT agent reports all tests passed
    When the PA evaluates the UAT result
    Then the PA dispatches a DevOpsBundle targeting "staging"
    And the story transitions to "staging"

  @failure @retry
  Scenario: Failed coding task is retried
    Given the coding agent reports failure with errors
    And the task's retry count is below max retries
    When the PA evaluates the result
    Then the PA creates a new coding task with the failure context
    And the new task's parent_task_id references the failed task
    And the story remains in "in_progress"

  @failure @escalation
  Scenario: Failed task after max retries escalates to stakeholder
    Given the coding agent reports failure
    And the task's retry count equals max retries
    When the PA evaluates the result
    Then the PA escalates to the stakeholder
    And the escalation includes the task description and error history

  @failure @pr-rejected
  Scenario: PR rejection routes back to coding
    Given the PR agent requested changes on the PR
    When the PA evaluates the PR result
    Then the PA dispatches a new CodingBundle with the review feedback
    And the story transitions back to "in_progress"

  @failure @uat-failed
  Scenario: UAT failure routes back to coding
    Given the UAT agent reports test failures
    When the PA evaluates the UAT result
    Then the PA dispatches a new CodingBundle with the UAT failure details
    And the story transitions back to "in_progress"

  @edge-case
  Scenario: Each routing step follows the dispatch-interrupt-resume pattern
    Given any dispatch node runs
    When the agent is dispatched
    Then the graph interrupts and checkpoints state
    And the graph only resumes when a callback arrives
