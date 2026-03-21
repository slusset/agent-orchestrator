---
id: feature-request-lifecycle
type: journey
refs:
  persona: specs/personas/stakeholder.md
---

# Journey: Feature Request Lifecycle

## Actor
Stakeholder — product owner requesting a new feature.
Source Persona: specs/personas/stakeholder.md

## Trigger
Stakeholder describes a feature they want built.

## Preconditions
- Orchestrator is running and accessible
- At least one Coding Agent is registered and available
- Repository is configured and accessible
- Stakeholder has a way to communicate with the PA (chat, API, etc.)

## Flow

### 1. Request Feature
- **User intent**: Describe what they want built in natural language
- **System response**: PA acknowledges the request and begins planning
- **Next**: Plan Work

### 2. Plan Work
- **User intent**: PA decomposes the request into actionable tasks
- **System response**: PA pulls context from JIRA/Confluence/project data, identifies which agents are needed, creates a structured plan
- **Next**: Dispatch to Coding Agent

### 3. Dispatch to Coding Agent
- **User intent**: PA sends a CodingBundle with concrete instructions
- **System response**: Coding Agent receives the bundle, begins working. PA records the dispatch and begins monitoring.
- **Next**: Monitor Progress

### 4. Monitor Progress
- **User intent**: PA watches for heartbeats and escalates if agent goes silent
- **System response**: Agent sends periodic heartbeats. PA updates internal state. If timeout threshold crossed, PA nudges or escalates.
- **Next**: Receive PR (or Handle Timeout)

### 5. Receive PR
- **User intent**: Coding Agent signals completion by creating a PR
- **System response**: PA receives callback with PR URL, resumes graph execution, evaluates result
- **Next**: Dispatch to PR Agent

### 6. PR Review
- **User intent**: PA dispatches PR to PR Agent for automated review
- **System response**: PR Agent reviews against acceptance criteria and coding standards. Approves or requests changes.
- **Next**: Merge PR (if approved) or Send Back (if changes needed)

### 7. Merge PR
- **User intent**: PR Agent merges the approved PR
- **System response**: PR is merged, branch is cleaned up
- **Next**: Dispatch to UAT Agent

### 8. UAT Validation
- **User intent**: PA dispatches to UAT Agent to validate the merged feature
- **System response**: UAT Agent tests against acceptance criteria, user stories, and journeys. Reports pass/fail with evidence.
- **Next**: Dispatch to DevOps (if pass) or Route Back to Coding (if fail)

### 9. Deploy to Staging
- **User intent**: PA dispatches to DevOps Agent for staging deployment
- **System response**: DevOps Agent deploys, runs health checks, reports success/failure
- **Next**: Notify Stakeholder

### 10. Notify Stakeholder
- **User intent**: PA informs stakeholder that the feature is on staging and ready for review
- **System response**: Stakeholder receives summary: what was built, PR link, staging URL, test results
- **Next**: Stakeholder Approves or Requests Changes

### 11. Stakeholder Approval
- **User intent**: Stakeholder reviews on staging and approves for production
- **System response**: PA dispatches production deployment to DevOps Agent
- **Next**: Production Deploy

### 12. Production Deploy
- **User intent**: DevOps Agent deploys to production
- **System response**: Deploy completes, health checks pass, PA closes the story
- **Next**: Story Complete

## Outcomes
- **Success**: Feature is live in production, story is closed, stakeholder is notified
- **Failure modes**:
  - Coding Agent fails tests → retry with feedback
  - PR review requests changes → Coding Agent iterates
  - UAT fails → Coding Agent fixes, re-enters pipeline
  - Deployment fails → DevOps Agent troubleshoots or rolls back
  - Agent goes silent → PA escalates to stakeholder

## Related Stories
- specs/stories/orchestration/stakeholder-requests-feature.md
- specs/stories/orchestration/pa-plans-and-dispatches.md
- specs/stories/orchestration/pa-evaluates-and-routes.md
- specs/stories/orchestration/stakeholder-approves-deploy.md

## E2E Coverage
- (to be created)
