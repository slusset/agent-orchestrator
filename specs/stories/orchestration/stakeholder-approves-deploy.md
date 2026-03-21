---
id: stakeholder-approves-deploy
type: story
refs:
  journey: specs/journeys/feature-request-lifecycle.md
  persona: specs/personas/stakeholder.md
  steps: [10, 11, 12]
---

# Story: Stakeholder Approves Production Deployment

## Narrative
As a Stakeholder,
I want to review the feature on staging and approve it for production,
So that nothing goes to production without my explicit approval.

## Acceptance Criteria
- [ ] PA notifies stakeholder when feature is deployed to staging
- [ ] Notification includes: summary of changes, PR link, staging URL, test results
- [ ] Stakeholder can approve or reject the deployment
- [ ] On approval: PA dispatches production deployment to DevOps Agent
- [ ] On rejection: PA routes feedback back to Coding Agent for iteration
- [ ] PA closes the story/ticket after successful production deployment

## Notes
- Production deployments should always require stakeholder approval (requires_approval=True)
- The approval mechanism could be a chat response, a webhook, or a JIRA transition
- Rollback plan should be communicated with the approval request
