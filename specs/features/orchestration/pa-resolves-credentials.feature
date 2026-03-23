# id: pa-resolves-credentials
# type: feature
# story: specs/stories/orchestration/pa-resolves-credentials.md
# journey: specs/journeys/feature-request-lifecycle.md
# model: specs/models/project/credentials.model.yaml

@orchestration @credentials
Feature: PA resolves credentials before dispatching to agents
  As the Primary Agent
  I want to resolve project credentials and inject them into TaskBundles
  So that agents can operate with the secrets they need without managing auth

  Background:
    Given a project credential manifest at ".pm/credentials.yaml"
    And the manifest declares "ANTHROPIC_API_KEY" as required for roles "coding, pr, uat"
    And the manifest declares "GITHUB_TOKEN" as required for roles "coding, pr"
    And the manifest declares "JIRA_TOKEN" as optional

  # --- Boot-time validation ---

  @boot
  Scenario: PA validates credentials at boot with all required present
    Given the credential provider can resolve "ANTHROPIC_API_KEY" and "GITHUB_TOKEN"
    When the PA boots
    Then credential validation succeeds
    And the PA is ready to accept work

  @boot @warning
  Scenario: PA warns when required credentials are missing
    Given the credential provider cannot resolve "GITHUB_TOKEN"
    When the PA boots
    Then credential validation reports "GITHUB_TOKEN" as missing
    And the PA logs a warning with the resolution summary
    And the PA still accepts work (graceful degradation)

  @boot @no-manifest
  Scenario: PA operates without credential manifest
    Given no credential manifest exists
    When the PA boots
    Then credential validation is skipped
    And the PA is ready to accept work with empty resolved credentials

  # --- Provider chain ---

  @chain
  Scenario: Chain provider resolves with priority fallback
    Given a chain provider with DotEnv then Env backends
    And the ".env" file contains "ANTHROPIC_API_KEY=from-dotenv"
    And the environment variable "ANTHROPIC_API_KEY" is set to "from-env"
    When credentials are resolved
    Then "ANTHROPIC_API_KEY" is resolved from the "dotenv" provider
    And the environment variable value is not used

  @chain
  Scenario: Chain provider falls through on missing key
    Given a chain provider with DotEnv then Env backends
    And the ".env" file does not contain "GITHUB_TOKEN"
    And the environment variable "GITHUB_TOKEN" is set to "from-env"
    When credentials are resolved
    Then "GITHUB_TOKEN" is resolved from the "env" provider

  # --- Role-based filtering ---

  @role-filter
  Scenario: Coding agent receives only coding-role credentials
    Given all credentials are resolved
    When the PA dispatches a CodingBundle
    Then the bundle's resolved_env contains "ANTHROPIC_API_KEY"
    And the bundle's resolved_env contains "GITHUB_TOKEN"
    And the bundle's resolved_env does not contain "JIRA_TOKEN"

  @role-filter
  Scenario: UAT agent receives only UAT-role credentials
    Given all credentials are resolved
    When the PA dispatches a UATBundle
    Then the bundle's resolved_env contains "ANTHROPIC_API_KEY"
    And the bundle's resolved_env does not contain "GITHUB_TOKEN"

  # --- Serialization safety ---

  @security
  Scenario: resolved_env is excluded from serialization
    Given a TaskBundle with resolved_env containing "SECRET_KEY=secret_value"
    When the bundle is serialized to JSON
    Then "resolved_env" does not appear in the output
    And "secret_value" does not appear in the output

  @security
  Scenario: resolved_env does not survive LangGraph checkpointing
    Given the graph context contains per-role credential dicts
    When the graph checkpoints state
    Then credential values are stored as plain dicts (not provider objects)
    And credentials are re-resolved from provider on next boot

  # --- Agent subprocess injection ---

  @integration
  Scenario: CodingAgent passes credentials to CLI subprocess
    Given a CodingBundle with resolved_env "ANTHROPIC_API_KEY=sk-test"
    When the CodingAgent creates an AgentCLI
    Then the CLI's extra_env contains "ANTHROPIC_API_KEY=sk-test"
    And the subprocess environment merges resolved_env with os.environ

  # --- SOPS provider ---

  @sops
  Scenario: SOPS provider decrypts dotenv-format secrets
    Given a SOPS-encrypted file "secrets.env" with dotenv content
    When the SOPS provider resolves credentials
    Then credentials are decrypted via "sops --decrypt"
    And the decrypted output is parsed as key=value pairs

  @sops @error
  Scenario: SOPS provider reports decryption failure
    Given a SOPS-encrypted file with an invalid encryption key
    When the SOPS provider attempts to resolve credentials
    Then a RuntimeError is raised with the sops error message
    And the credential is recorded as an error in the resolution result

  # --- DotEnv provider ---

  @dotenv
  Scenario: DotEnv provider reads .env file
    Given a ".env" file with "API_KEY=my-secret"
    When the DotEnv provider resolves "API_KEY"
    Then the value "my-secret" is returned

  @dotenv
  Scenario: DotEnv provider handles missing file gracefully
    Given no ".env" file exists
    When the DotEnv provider resolves any credential
    Then None is returned (no error raised)
