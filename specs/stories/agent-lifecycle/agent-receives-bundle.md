---
id: agent-receives-bundle
type: story
refs:
  journey: specs/journeys/agent-execution-lifecycle.md
  persona: specs/personas/coding-agent.md
  steps: [1, 2]
---

# Story: Agent Receives and Validates a TaskBundle

## Narrative
As a Coding Agent,
I want to receive a TaskBundle with everything I need to work independently,
So that I can start executing without needing to ask the PA for clarification.

## Acceptance Criteria
- [ ] Agent receives a TaskBundle via its invocation endpoint
- [ ] Agent validates the bundle (required fields, valid URLs, reachable callback)
- [ ] Agent extracts objective, acceptance criteria, and scope configuration
- [ ] Agent initializes its workspace (clone repo, create branch, etc.)
- [ ] If bundle validation fails, agent reports failure immediately via callback
- [ ] Agent does not need to know about the PA's internal state or methodology

## Notes
- The agent receives a StatusReporter (injected), not a raw callback_url
- Workspace setup is agent-type-specific (repo checkout vs. environment setup vs. provider config)
- Bundle validation should catch issues early rather than failing mid-execution
