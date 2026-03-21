# id: stakeholder-approves-deploy
# type: feature
# story: specs/stories/orchestration/stakeholder-approves-deploy.md
# journey: specs/journeys/feature-request-lifecycle.md
# model: specs/models/story/story.lifecycle.yaml

@orchestration
Feature: Stakeholder approves production deployment
  As a Stakeholder
  I want to review the feature on staging and approve it for production
  So that nothing goes to production without my explicit approval

  Background:
    Given the orchestrator is running
    And a story exists with a feature deployed to staging
    And the story is in "staging" status

  @happy-path
  Scenario: PA notifies stakeholder when staging deploy completes
    Given the DevOps agent reports staging deployment succeeded
    And the health check passed
    When the PA evaluates the deployment result
    Then the PA sends a notification to the stakeholder
    And the notification includes a summary of changes
    And the notification includes the PR link
    And the notification includes the staging URL
    And the notification includes the UAT test results
    And the story transitions to "awaiting_approval"

  @happy-path
  Scenario: Stakeholder approves production deployment
    Given the story is in "awaiting_approval" status
    When the stakeholder approves the deployment
    Then the PA dispatches a DevOpsBundle targeting "production"
    And the DevOpsBundle has requires_approval set to true
    And the story transitions to "deploying"

  @happy-path
  Scenario: PA closes story after successful production deployment
    Given a production deployment task completed successfully
    When the PA evaluates the deployment result
    Then the story transitions to "completed"
    And the story's completed_at is set
    And the external ticket is closed

  @rejection
  Scenario: Stakeholder rejects deployment with feedback
    Given the story is in "awaiting_approval" status
    When the stakeholder rejects with feedback "Login button is misaligned"
    Then the PA dispatches a new CodingBundle with the rejection feedback
    And the story transitions back to "in_progress"

  @edge-case
  Scenario: Production deploy always requires approval
    Given any DevOpsBundle targeting "production"
    Then the bundle's requires_approval field is true
    And the DevOps agent pauses before final deploy and notifies the PA

  @validation
  Scenario: Story cannot skip staging to go directly to production
    Given the story is in "uat" status
    When the PA attempts to dispatch a production deployment
    Then the dispatch is rejected
    And the story remains in "uat"
