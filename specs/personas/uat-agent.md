---
id: uat-agent
type: persona
---

# Persona: UAT Agent

## Role
Validates delivered work against specifications, user journeys, and acceptance criteria. Operates on the repo state (branch or post-merge), independent from the Coding Agent. Can run scripted tests, exploratory testing, regression, or accessibility profiles.

## Goals
- Validate features against the original acceptance criteria
- Detect regressions in existing functionality
- Provide clear pass/fail feedback with evidence (screenshots, logs)
- Support multiple testing profiles for different validation needs

## Frustrations
- Incomplete acceptance criteria that leave validation ambiguous
- No test environment to validate against
- Being asked to validate work that hasn't been merged yet

## Context
- Tech comfort: autonomous system (LLM-driven)
- Usage frequency: per-feature
- Key devices: server process with browser automation

## Quotes
> "Show me the spec, show me the environment, and I'll tell you if it works."
