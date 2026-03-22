#!/usr/bin/env python3
"""
Plan validation script for CI.

Validates that plan/roadmap.yaml conforms to the plan schema
and all invariants hold. Run in CI to prevent drift.

Usage:
    uv run python tools/validate-plan.py
    uv run python tools/validate-plan.py --plan plan/roadmap.yaml
    uv run python tools/validate-plan.py --strict  # Also check spec_refs exist

Exit codes:
    0 = valid
    1 = validation errors
    2 = file not found / parse error
"""

import argparse
import sys
from pathlib import Path

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.plan.schema import Roadmap, validate_roadmap


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate plan/roadmap.yaml")
    parser.add_argument(
        "--plan",
        default="plan/roadmap.yaml",
        help="Path to roadmap YAML file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also verify that spec_refs and issue_refs point to real files/issues",
    )
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"✗ Plan file not found: {plan_path}")
        return 2

    # Parse YAML
    try:
        with open(plan_path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"✗ YAML parse error: {e}")
        return 2

    if not data:
        print("✗ Plan file is empty")
        return 2

    # Validate schema
    try:
        roadmap = Roadmap.model_validate(data)
    except Exception as e:
        print(f"✗ Schema validation failed: {e}")
        return 1

    # Run structural validation
    errors = validate_roadmap(roadmap)
    if errors:
        print(f"✗ {len(errors)} validation error(s):")
        for error in errors:
            print(f"  - {error}")
        return 1

    # Strict mode: check file references exist
    if args.strict:
        strict_errors = _check_refs(roadmap)
        if strict_errors:
            print(f"✗ {len(strict_errors)} reference error(s):")
            for error in strict_errors:
                print(f"  - {error}")
            return 1

    # Summary
    milestones = roadmap.milestones
    all_caps = roadmap.all_capabilities()
    complete_m = sum(1 for m in milestones if m.status.value == "complete")
    complete_c = sum(1 for _, c in all_caps if c.status.value == "complete")

    print(f"✓ Plan valid: {roadmap.name}")
    print(f"  Milestones: {complete_m}/{len(milestones)} complete")
    print(f"  Capabilities: {complete_c}/{len(all_caps)} complete")

    active = roadmap.active_milestones()
    if active:
        print(f"  Active: {', '.join(m.id for m in active)}")

    available = roadmap.available_milestones()
    if available:
        print(f"  Ready to start: {', '.join(m.id for m in available)}")

    return 0


def _check_refs(roadmap: Roadmap) -> list[str]:
    """Check that spec_refs point to existing files."""
    errors = []
    for milestone in roadmap.milestones:
        for cap in milestone.capabilities:
            for ref in cap.spec_refs:
                if not Path(ref).exists():
                    errors.append(
                        f"Capability '{cap.id}' references "
                        f"'{ref}' which does not exist"
                    )
    return errors


if __name__ == "__main__":
    sys.exit(main())
