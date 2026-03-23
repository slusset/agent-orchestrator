# Agent Orchestrator — Project Commands
# Run `just` to see all available targets

set dotenv-load := true

# Default: list all targets
default:
    @just --list --unsorted

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Run all tests
test *args='':
    uv run pytest {{ args }}

# Run tests with verbose output
test-v *args='':
    uv run pytest -v {{ args }}

# Run tests for a specific layer
test-plan:
    uv run pytest tests/plan/ -v

test-contracts:
    uv run pytest tests/contracts/ -v

test-orchestrator:
    uv run pytest tests/orchestrator/ -v

test-agents:
    uv run pytest tests/agents/ -v

test-evals:
    uv run pytest tests/evals/ -v

# Run tests matching a pattern
test-k pattern:
    uv run pytest -v -k "{{ pattern }}"

# Run tests with coverage (requires pytest-cov)
test-cov:
    uv run pytest --cov=src --cov=evals --cov-report=term-missing

# ---------------------------------------------------------------------------
# Evals
# ---------------------------------------------------------------------------

# Run eval suite with Claude Code
eval *args='':
    uv run python -m evals {{ args }}

# Run evals with verbose output
eval-v:
    uv run python -m evals -v

# Run a single eval task
eval-task task_id:
    uv run python -m evals --task {{ task_id }} -v

# Dry run — discover and validate tasks without running agents
eval-dry:
    uv run python -m evals --dry-run

# Run evals and save results
eval-save:
    uv run python -m evals --output evals/results/ -v

# Run evals with a specific CLI
eval-cli cli_type:
    uv run python -m evals --cli {{ cli_type }} -v

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Validate roadmap schema and structure
plan-check:
    uv run python tools/validate-plan.py

# Validate roadmap with strict mode (verify spec_refs exist on disk)
plan-check-strict:
    uv run python tools/validate-plan.py --strict

# Show roadmap status (milestones, progress, what's next)
plan-status:
    @uv run python -c "\
    from src.plan.manager import PlanManager; \
    pm = PlanManager('plan/roadmap.yaml'); \
    pm.load(); \
    ctx = pm.planning_context(); \
    print(f'Progress: {ctx[\"progress\"]}'); \
    active = [m[\"id\"] for m in ctx.get(\"active_milestones\", [])]; \
    avail = [m[\"id\"] for m in ctx.get(\"available_milestones\", [])]; \
    blocked = [m[\"id\"] for m in ctx.get(\"blocked_milestones\", [])]; \
    print(f'Active:    {active or \"none\"}'); \
    print(f'Available: {avail or \"none\"}'); \
    print(f'Blocked:   {blocked or \"none\"}'); \
    "

# Verify IDD spec references exist on disk
specs-check:
    @echo "Checking IDD spec references..."
    @for dir in specs/personas specs/journeys specs/stories specs/features specs/models specs/capabilities; do \
        if [ -d "$dir" ]; then \
            count=$(find "$dir" -type f \( -name "*.md" -o -name "*.yaml" -o -name "*.feature" \) | wc -l | tr -d ' '); \
            echo "  $dir: $count files"; \
        else \
            echo "  $dir: MISSING"; \
        fi; \
    done
    @echo ""
    @echo "Checking model aggregates..."
    @for dir in specs/models/*/; do \
        if [ -f "${dir}_aggregate.yaml" ]; then \
            echo "  $dir: ✓ has _aggregate.yaml"; \
        else \
            echo "  $dir: ✗ missing _aggregate.yaml"; \
        fi; \
    done

# Run all validation checks
check: plan-check specs-check
    @echo ""
    @echo "Running tests..."
    @uv run pytest -q

# Strict validation (all checks + spec_refs on disk + full test suite)
check-strict: plan-check-strict specs-check
    @echo ""
    @echo "Running full test suite..."
    @uv run pytest -v

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

# Install dependencies
install:
    uv sync

# Format and lint (if ruff is configured)
fmt:
    uv run ruff format src/ tests/ evals/ || echo "ruff not configured, skipping"

lint:
    uv run ruff check src/ tests/ evals/ || echo "ruff not configured, skipping"

# Run the demo (stub agent e2e loop)
demo:
    uv run python demo.py

# Compile the LangGraph graph and verify structure
graph-check:
    uv run python main.py

# ---------------------------------------------------------------------------
# Git & CI
# ---------------------------------------------------------------------------

# Show what's ahead of origin
ahead:
    @git log --oneline origin/main..HEAD 2>/dev/null || echo "No remote tracking"

# Show current status
status:
    @git status --short
    @echo ""
    @just plan-status

# Pre-push validation (what CI would run)
pre-push: check
    @echo ""
    @echo "✓ All checks passed — safe to push"
