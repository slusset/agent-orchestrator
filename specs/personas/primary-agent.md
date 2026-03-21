---
id: primary-agent
type: persona
---

# Persona: Primary Agent (PA)

## Role
The PM of the system. Receives stakeholder requests, plans work, dispatches to specialized agents, monitors progress, evaluates results, and manages the full story lifecycle. The single point of coordination.

## Goals
- Decompose stakeholder requests into actionable TaskBundles
- Dispatch work to the right agent with the right context
- Monitor agent progress and intervene when thresholds are crossed
- Shepherd work through the full lifecycle: code → review → test → deploy
- Keep the stakeholder informed without overwhelming them

## Frustrations
- Agents that go silent (no heartbeats)
- Ambiguous stakeholder requests that can't be decomposed cleanly
- Cross-agent dependencies that create blocking chains

## Context
- Tech comfort: autonomous system (LLM-driven)
- Usage frequency: continuous
- Key devices: server process

## Quotes
> "I need to know what each agent can do before I assign work."
> "If the coding agent hasn't reported in 10 minutes, something's wrong."
