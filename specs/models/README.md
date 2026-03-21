# Domain Glossary

## Ubiquitous Language

| Term | Definition | Context |
|------|------------|---------|
| Story | A stakeholder feature request tracked through its full lifecycle | Core |
| Task | A unit of work dispatched to a single agent | Core |
| TaskBundle | The hand-off contract carrying everything an agent needs to work independently | Core |
| Agent | A specialized autonomous worker (Coding, UAT, DevOps, PR) | Core |
| Primary Agent (PA) | The orchestrator/PM that coordinates all agents | Core |
| Stakeholder | The human product owner who requests features and approves deployments | Core |
| StatusUpdate | A heartbeat or progress report from an agent to the PA | Core |
| TaskResult | The completion payload from an agent (success/failure + artifacts) | Core |
| Callback | The mechanism by which an agent reports back to the PA | Infrastructure |
| Heartbeat | A periodic "still alive" signal from agent to PA | Core |
| Escalation | PA notifying stakeholder when a task is stuck beyond thresholds | Core |
| Capability Profile | What the PA knows about an agent's skills and constraints | Core |
| Deployment | The act of releasing code to an environment | Core |
| Artifact | A deliverable produced by an agent (PR URL, deployment URL, test report) | Core |

## Aggregates

| Aggregate | Root | Description |
|-----------|------|-------------|
| Story | Story | Feature request lifecycle from intake to production |
| Task | Task | Individual agent work assignment and its lifecycle |
| Agent | Agent | Agent registration, capabilities, and availability |
| Deployment | Deployment | Release to a target environment |

## Bounded Contexts

| Context | Description | Key Aggregates |
|---------|-------------|----------------|
| Orchestration | Story lifecycle and agent coordination | Story, Task |
| Agent Management | Agent registration and capability tracking | Agent |
| Deployment | Environment management and release coordination | Deployment |
| Integration | External system adapters (GitHub, JIRA, Vercel, etc.) | — |
