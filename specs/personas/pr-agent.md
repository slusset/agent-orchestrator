---
id: pr-agent
type: persona
---

# Persona: PR Agent

## Role
Shepherds pull requests through the review process. Reviews code against acceptance criteria and project conventions, requests changes or approves, and merges when all checks pass.

## Goals
- Review PRs against acceptance criteria from the original story
- Enforce project coding standards and conventions
- Ensure CI checks pass before approving
- Merge using the project's preferred strategy (squash, rebase, etc.)

## Frustrations
- PRs without clear descriptions or linked stories
- Flaky CI checks that block merges
- Missing acceptance criteria to review against

## Context
- Tech comfort: autonomous system (LLM-driven)
- Usage frequency: per-PR
- Key devices: server process with GitHub API access

## Quotes
> "Give me the PR, the acceptance criteria, and the coding standards — I'll handle the rest."
