---
id: pa-plans-and-dispatches
type: story
refs:
  journey: specs/journeys/feature-request-lifecycle.md
  persona: specs/personas/primary-agent.md
  steps: [2, 3, 4]
---

# Story: PA Plans Work and Dispatches to Agents

## Narrative
As the Primary Agent,
I want to decompose a stakeholder request into TaskBundles and dispatch them to the right agents,
So that work proceeds in parallel where possible and each agent gets exactly the context it needs.

## Acceptance Criteria
- [ ] PA creates a CodingBundle with objective, acceptance criteria, repo info, and scope boundaries
- [ ] PA includes a callback_url in the bundle for the agent to report back
- [ ] PA records the dispatched task in its internal state with status "dispatched"
- [ ] PA's graph interrupts after dispatch, waiting for the agent's callback
- [ ] PA can dispatch to multiple agents in sequence (coding → PR → UAT → devops)

## Notes
- The PA distills methodology (IDD) into concrete instructions — agents don't need to know about IDD
- Skills in the bundle are suggestions, not commands — agents use their implicit capabilities
- The dispatch mechanism (HTTP, subprocess, queue) is abstracted by the dispatcher
