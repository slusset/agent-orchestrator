# Agent Rules

Project-level rules injected into every CodingBundle context.
The PA reads this at boot and includes it in dispatched tasks.
Agents follow these rules alongside the objective and acceptance criteria.

## Git Hygiene

- Always create or verify a `.gitignore` exists before committing
- Never commit compiled artifacts: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `node_modules/`
- Never commit IDE/editor files: `.vscode/`, `.idea/`, `*.swp`
- Never commit environment files: `.env`, `.env.local`
- If the repo lacks a `.gitignore`, create one appropriate for the project language

## Code Style

- Follow existing project conventions (indentation, naming, imports)
- Do not add unnecessary comments or docstrings to code you didn't write
- Keep changes focused — only modify what the objective requires

## Testing

- Tests must pass before creating a PR
- Place tests in the `tests/` directory mirroring the source structure
- Use pytest unless the project already uses a different framework

## Pull Requests

- PRs should be draft by default
- PR body should describe what changed and why
- Do not force-push or rewrite history on shared branches
