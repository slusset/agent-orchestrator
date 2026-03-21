---
id: stakeholder-requests-feature
type: story
refs:
  journey: specs/journeys/feature-request-lifecycle.md
  persona: specs/personas/stakeholder.md
  steps: [1, 2]
---

# Story: Stakeholder Requests a Feature

## Narrative
As a Stakeholder,
I want to describe a feature in natural language and have the PA plan the work,
So that I don't need to manually break down tasks or coordinate agents.

## Acceptance Criteria
- [ ] Stakeholder can send a feature request as a natural language message
- [ ] PA acknowledges receipt and begins planning
- [ ] PA pulls relevant context from configured sources (JIRA, project data)
- [ ] PA produces a structured plan identifying which agents are needed
- [ ] PA communicates the plan back to the stakeholder before dispatching

## Notes
- The PA should ask clarifying questions if the request is ambiguous
- Plan should identify acceptance criteria even if the stakeholder didn't specify them
- Context sources are pluggable (JIRA, Confluence, local specs, etc.)
