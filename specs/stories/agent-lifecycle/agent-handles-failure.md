---
id: agent-handles-failure
type: story
refs:
  journey: specs/journeys/agent-execution-lifecycle.md
  persona: specs/personas/coding-agent.md
  steps: [4, 5, 6]
---

# Story: Agent Handles Failures Gracefully

## Narrative
As a Coding Agent,
I want to handle failures at each phase and report them clearly,
So that the PA can make informed decisions about retrying or escalating.

## Acceptance Criteria
- [ ] If workspace setup fails (bad repo URL, auth issues), agent reports failure immediately
- [ ] If implementation fails, agent reports what was attempted and the specific error
- [ ] If tests fail, agent attempts to fix once before reporting failure
- [ ] If tests still fail after fix attempt, agent reports failure with test output
- [ ] All failures include enough context for the PA to decide on next steps
- [ ] Agent cleans up workspace (delete branch, etc.) on failure where appropriate

## Notes
- The BaseAgent.run() method catches exceptions from execute() and calls reporter.fail()
- Test failures are a special case — worth one retry before giving up
- Error messages should be actionable, not just stack traces
