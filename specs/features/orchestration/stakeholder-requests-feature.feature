# id: stakeholder-requests-feature
# type: feature
# story: specs/stories/orchestration/stakeholder-requests-feature.md
# journey: specs/journeys/feature-request-lifecycle.md
# model: specs/models/story/story.model.yaml

@orchestration
Feature: Stakeholder requests a feature
  As a Stakeholder
  I want to describe a feature in natural language and have the PA plan the work
  So that I don't need to manually break down tasks or coordinate agents

  Background:
    Given the orchestrator is running
    And at least one coding agent is registered and available

  @happy-path
  Scenario: Stakeholder sends a feature request and receives a plan
    Given the stakeholder sends a message "Add user authentication with email/password"
    When the PA receives the request
    Then the PA acknowledges receipt
    And the PA creates a story in "intake" status
    And the story transitions to "planning"
    And the PA pulls context from configured sources
    And the PA produces a structured plan identifying the coding agent
    And the PA communicates the plan back to the stakeholder

  @happy-path
  Scenario: PA derives acceptance criteria from stakeholder request
    Given the stakeholder sends a message without explicit acceptance criteria
    When the PA plans the work
    Then the plan includes derived acceptance criteria
    And each criterion is testable in plain language

  @context-sources
  Scenario: PA pulls context from JIRA
    Given a JIRA ticket "PROJ-42" exists with specifications
    And the stakeholder sends a message referencing "PROJ-42"
    When the PA plans the work
    Then the story's context includes the JIRA ticket details
    And the story's external_ref is set to "PROJ-42"

  @edge-case
  Scenario: PA asks for clarification on ambiguous request
    Given the stakeholder sends an ambiguous message "make it better"
    When the PA attempts to plan the work
    Then the PA asks the stakeholder for clarification
    And the story remains in "intake" status until clarified

  @validation
  Scenario: PA rejects empty feature request
    Given the stakeholder sends an empty message
    When the PA receives the request
    Then the PA responds with a prompt for a feature description
    And no story is created
