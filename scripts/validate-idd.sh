#!/usr/bin/env bash
# Validate IDD spec structure and traceability locally.
# Usage: ./scripts/validate-idd.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

errors=0
warnings=0

echo ""
echo "=========================================="
echo "  IDD Validation"
echo "=========================================="
echo ""

# --- 1. Capability file references ---
echo "1. Validating capability file references..."
for cap_file in specs/capabilities/*.capability.yaml; do
  [ -f "$cap_file" ] || continue
  echo "   Capability: $cap_file"

  for ref in $(grep -o 'specs/[^ ]*' "$cap_file"); do
    if [ ! -f "$ref" ]; then
      echo -e "   ${RED}ERROR${NC}: Missing: $ref"
      errors=$((errors + 1))
    fi
  done
done
echo ""

# --- 2. Front-matter refs in journeys ---
echo "2. Validating journey front-matter refs..."
for journey_file in specs/journeys/*.md; do
  [ -f "$journey_file" ] || continue
  persona_ref=$(grep -A1 'refs:' "$journey_file" 2>/dev/null | grep 'persona:' | sed 's/.*persona: //' | tr -d ' ' || true)
  if [ -n "$persona_ref" ] && [ ! -f "$persona_ref" ]; then
    echo -e "   ${RED}ERROR${NC}: $journey_file → missing persona: $persona_ref"
    errors=$((errors + 1))
  else
    echo -e "   ${GREEN}OK${NC}: $journey_file"
  fi
done
echo ""

# --- 3. Front-matter refs in stories ---
echo "3. Validating story front-matter refs..."
for story_file in $(find specs/stories -name '*.md' 2>/dev/null); do
  [ -f "$story_file" ] || continue
  journey_ref=$(grep -A2 'refs:' "$story_file" 2>/dev/null | grep 'journey:' | sed 's/.*journey: //' | tr -d ' ' || true)
  persona_ref=$(grep -A3 'refs:' "$story_file" 2>/dev/null | grep 'persona:' | sed 's/.*persona: //' | tr -d ' ' || true)

  ok=true
  if [ -n "$journey_ref" ] && [ ! -f "$journey_ref" ]; then
    echo -e "   ${RED}ERROR${NC}: $story_file → missing journey: $journey_ref"
    errors=$((errors + 1))
    ok=false
  fi
  if [ -n "$persona_ref" ] && [ ! -f "$persona_ref" ]; then
    echo -e "   ${RED}ERROR${NC}: $story_file → missing persona: $persona_ref"
    errors=$((errors + 1))
    ok=false
  fi
  if $ok; then
    echo -e "   ${GREEN}OK${NC}: $story_file"
  fi
done
echo ""

# --- 4. BDD feature coverage ---
echo "4. Checking BDD feature coverage..."
story_count=0
covered_count=0

for story_file in $(find specs/stories -name '*.md' 2>/dev/null); do
  [ -f "$story_file" ] || continue
  story_count=$((story_count + 1))
  story_id=$(grep '^id:' "$story_file" | sed 's/id: //' | tr -d ' ')

  if [ -d "specs/features" ] && grep -rl "$story_id" specs/features/ 2>/dev/null | grep -q '.feature'; then
    covered_count=$((covered_count + 1))
  else
    echo -e "   ${YELLOW}WARN${NC}: No feature file for: $story_id"
    warnings=$((warnings + 1))
  fi
done

echo ""
echo "   Stories: $story_count"
echo "   With BDD coverage: $covered_count"
echo "   Without coverage: $((story_count - covered_count))"
echo ""

# --- Summary ---
echo "=========================================="
if [ $errors -gt 0 ]; then
  echo -e "  ${RED}FAILED${NC}: $errors errors, $warnings warnings"
  exit 1
else
  echo -e "  ${GREEN}PASSED${NC}: 0 errors, $warnings warnings"
fi
echo "=========================================="
echo ""
