---
id: devops-agent
type: persona
---

# Persona: DevOps Agent

## Role
Manages deployments across environments (dev, UAT, staging, production), handles secrets, coordinates infrastructure providers (Vercel, Railway, Supabase), and troubleshoots deployment failures.

## Goals
- Deploy reliably to the target environment
- Manage secrets and environment variables safely
- Coordinate multi-provider deployments atomically
- Rollback cleanly when deployments fail
- Troubleshoot infrastructure issues with logs and diagnostics

## Frustrations
- Missing or stale secrets
- Deployments that partially succeed across providers
- No health check endpoint to validate deployments

## Context
- Tech comfort: autonomous system (LLM-driven)
- Usage frequency: per-deployment
- Key devices: server process with CLI access

## Quotes
> "I need the secrets refs, the target environment, and a health check URL."
