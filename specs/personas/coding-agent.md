---
id: coding-agent
type: persona
---

# Persona: Coding Agent (CA)

## Role
A specialized developer agent that implements features in isolation. Receives a CodingBundle, works on a branch, creates tests, and signals completion by creating a Pull Request.

## Goals
- Understand the objective and acceptance criteria clearly
- Implement changes within scope boundaries (focus_paths, protected_paths)
- Write tests that validate the acceptance criteria
- Create a clean PR that's ready for review

## Frustrations
- Vague objectives without clear acceptance criteria
- Missing context about existing code patterns
- Being asked to touch files outside its scope

## Context
- Tech comfort: autonomous system (LLM-driven)
- Usage frequency: per-task
- Key devices: server process / subprocess

## Quotes
> "Tell me exactly what to build, where to build it, and how to know it's done."
> "I don't need to know about IDD — just give me concrete instructions."
