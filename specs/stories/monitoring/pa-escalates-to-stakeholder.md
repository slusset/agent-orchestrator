---
id: pa-escalates-to-stakeholder
type: story
refs:
  journey: specs/journeys/watchdog-escalation.md
  persona: specs/personas/primary-agent.md
  steps: [4, 5]
---

# Story: PA Escalates to Stakeholder

## Narrative
As the Primary Agent,
I want to notify the stakeholder when a task is stuck and I can't resolve it,
So that they can make a decision about how to proceed.

## Acceptance Criteria
- [ ] PA sends escalation notification with: task description, time stuck, what was tried
- [ ] Notification includes options: retry, reassign, cancel, or provide guidance
- [ ] Stakeholder can respond with a decision
- [ ] PA acts on the stakeholder's decision (retry, cancel, etc.)
- [ ] If stakeholder doesn't respond within a threshold, PA takes a default action

## Notes
- Escalation is a last resort — PA should exhaust retry options first
- The notification channel is configurable (chat, email, JIRA comment, etc.)
- Default action on no response could be: cancel task and create a JIRA ticket
