---
id: pa-evaluates-and-routes
type: story
refs:
  journey: specs/journeys/feature-request-lifecycle.md
  persona: specs/personas/primary-agent.md
  steps: [5, 6, 7, 8, 9]
---

# Story: PA Evaluates Results and Routes to Next Agent

## Narrative
As the Primary Agent,
I want to evaluate an agent's result and automatically route to the next step in the pipeline,
So that the story progresses through the full lifecycle without manual intervention.

## Acceptance Criteria
- [ ] PA resumes from interrupt when an agent's callback arrives
- [ ] PA evaluates the TaskResult: checks success, reviews artifacts, validates against acceptance criteria
- [ ] On coding success: PA routes to PR Agent for review
- [ ] On PR approval: PA routes to UAT Agent for validation
- [ ] On UAT pass: PA routes to DevOps Agent for deployment
- [ ] On any failure: PA decides to retry, reassign, or escalate to stakeholder
- [ ] Each routing step follows the same pattern: dispatch → interrupt → resume → evaluate

## Notes
- The routing logic is a LangGraph conditional edge based on agent_type and result.success
- Failed tasks should include enough context for the PA to decide on retry vs. escalate
- The PA can short-circuit the pipeline (e.g., skip UAT for hotfixes) based on priority
