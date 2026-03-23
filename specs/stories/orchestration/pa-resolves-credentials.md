---
id: pa-resolves-credentials
type: story
refs:
  journey: specs/journeys/feature-request-lifecycle.md
  persona: specs/personas/primary-agent.md
  steps: [2, 3]
---

# Story: PA Resolves Credentials Before Dispatch

## Narrative
As the Primary Agent,
I want to resolve project credentials at boot and inject them into TaskBundles before dispatch,
So that agents receive the secrets they need to operate without managing their own auth.

## Acceptance Criteria
- [ ] PA loads the credential manifest from `.pm/credentials.yaml` at boot
- [ ] PA validates that all required credentials can be resolved
- [ ] PA warns (but does not hard-fail) when optional credentials are missing
- [ ] PA resolves credentials through a configurable provider chain (SOPS → .env → env vars)
- [ ] PA filters credentials by role — coding agents only receive coding-role secrets
- [ ] PA injects resolved credentials into `TaskBundle.resolved_env` before dispatch
- [ ] `resolved_env` is excluded from serialization — never appears in logs, JSON, or checkpoints
- [ ] Agents receive credentials in subprocess environment via `AgentCLI.env`
- [ ] If no manifest exists, PA operates without credential resolution (backward-compatible)

## Notes
- The manifest declares WHAT is needed; providers resolve WHERE to get it
- ChainProvider composes backends with priority fallback: SOPS → DotEnv → Env
- Credential values are never stored in LangGraph checkpoints — they're resolved fresh at boot
- Per-role filtering prevents credential sprawl (e.g., DevOps agent doesn't get ANTHROPIC_API_KEY)
