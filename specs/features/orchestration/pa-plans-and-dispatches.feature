# id: pa-plans-and-dispatches
# type: feature
# story: specs/stories/orchestration/pa-plans-and-dispatches.md
# journey: specs/journeys/feature-request-lifecycle.md
# model: specs/models/task/task.model.yaml

@orchestration
Feature: PA plans work and dispatches to agents
  As the Primary Agent
  I want to decompose a stakeholder request into TaskBundles and dispatch them to the right agents
  So that work proceeds and each agent gets exactly the context it needs

  Background:
    Given the orchestrator is running
    And a coding agent is registered with capability profile
    And a story exists in "planning" status with objective and acceptance criteria

  @happy-path
  Scenario: PA creates and dispatches a CodingBundle
    Given the story has objective "Add user authentication"
    And the story has acceptance criteria
    And the story targets repo "git@github.com:org/app.git"
    When the PA dispatches to the coding agent
    Then a CodingBundle is created with the story's objective
    And the bundle includes the acceptance criteria
    And the bundle includes the repo URL and base branch
    And the bundle includes a callback_url pointing to the PA
    And the bundle includes scope boundaries from the story
    And a task is recorded in state with status "dispatched"
    And the story transitions to "in_progress"

  @happy-path
  Scenario: PA's graph interrupts after dispatch
    Given the PA has dispatched a CodingBundle
    When the task is recorded in state
    Then the graph execution pauses at the interrupt point
    And the state is checkpointed
    And the PA is idle, waiting for the agent's callback

  @happy-path
  Scenario: PA dispatches agents in sequence through the pipeline
    Given a coding task has completed successfully with a PR URL
    When the PA evaluates the result
    Then the PA dispatches a PRBundle to the PR agent
    And when PR review completes, the PA dispatches a UATBundle
    And when UAT passes, the PA dispatches a DevOpsBundle

  @capability-matching
  Scenario: PA consults agent capability profile before dispatch
    Given the story requires TypeScript and Next.js skills
    And the coding agent's profile lists "typescript" and "nextjs" as implicit skills
    When the PA plans the dispatch
    Then the PA selects the coding agent
    And the bundle's skills field is empty because the agent knows these implicitly

  @capability-matching
  Scenario: PA includes configurable skill instructions in bundle
    Given the story requires BDD specifications
    And the coding agent lists "bdd-specs" as a configurable skill
    When the PA plans the dispatch
    Then the bundle's skills field includes "bdd-specs"
    And the bundle's context includes specific BDD instructions

  @validation
  Scenario: PA validates bundle before dispatch
    Given a story with no repo URL configured
    When the PA attempts to dispatch
    Then the PA reports an error to the stakeholder
    And no task is created
    And the story remains in "planning" status
