#!/usr/bin/env bash
# =============================================================================
# Pre-commit checklist hook
#
# Install:
#   cp scripts/pre-commit-checklist.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# Or use the .pre-commit-config.yaml with the `pre-commit` tool.
#
# This script runs a lightweight subset of checks that should complete in
# under 30 seconds.  Full test suites should be run via scripts/run_all_tests.sh.
# =============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO_ROOT="$(git rev-parse --show-toplevel)"
FAILED=0

echo -e "${YELLOW}━━━ Pre-commit Checklist ━━━${NC}"

# ────────────────────────────────────────────────────────────────────────────
# 1. Reject staged test-reports or .env files
# ────────────────────────────────────────────────────────────────────────────
BLOCKED_FILES=$(git diff --cached --name-only | grep -E '(^test-reports/|\.env\.|TEST_REPORT\.md$)' || true)
if [ -n "$BLOCKED_FILES" ]; then
    echo -e "${RED}✗ BLOCKED FILES — these should not be committed:${NC}"
    echo "$BLOCKED_FILES" | sed 's/^/    /'
    FAILED=1
fi

# ────────────────────────────────────────────────────────────────────────────
# 2. Backend lint (ruff)
# ────────────────────────────────────────────────────────────────────────────
STAGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
if [ -n "$STAGED_PY" ]; then
    echo -n "  Ruff lint... "
    if command -v ruff &> /dev/null; then
        if ruff check --quiet $STAGED_PY 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗ ruff found issues${NC}"
            FAILED=1
        fi
    else
        echo -e "${YELLOW}⊘ ruff not installed, skipping${NC}"
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 3. Frontend TypeScript check
# ────────────────────────────────────────────────────────────────────────────
STAGED_TS=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(tsx?|jsx?)$' || true)
if [ -n "$STAGED_TS" ]; then
    echo -n "  TypeScript check... "
    if [ -f "$REPO_ROOT/frontend/tsconfig.json" ]; then
        if (cd "$REPO_ROOT/frontend" && npx tsc --noEmit --pretty false 2>/dev/null); then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗ TypeScript errors${NC}"
            FAILED=1
        fi
    else
        echo -e "${YELLOW}⊘ No tsconfig.json, skipping${NC}"
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 4. Quick backend unit tests (< 15s)
# ────────────────────────────────────────────────────────────────────────────
if [ -n "$STAGED_PY" ]; then
    echo -n "  Backend unit tests... "
    if (cd "$REPO_ROOT/backend" && python -m pytest tests/unit -x -q --tb=no --no-header 2>/dev/null); then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${RED}✗ Unit tests failed${NC}"
        FAILED=1
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 5. Secret scan (gitleaks, if available)
# ────────────────────────────────────────────────────────────────────────────
echo -n "  Secret scan... "
if command -v gitleaks &> /dev/null; then
    if gitleaks protect --staged --no-banner --quiet 2>/dev/null; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${RED}✗ Potential secrets detected!${NC}"
        FAILED=1
    fi
else
    echo -e "${YELLOW}⊘ gitleaks not installed, skipping${NC}"
fi

# ────────────────────────────────────────────────────────────────────────────
# Result
# ────────────────────────────────────────────────────────────────────────────
echo ""
if [ $FAILED -ne 0 ]; then
    echo -e "${RED}━━━ Pre-commit FAILED — fix issues above ━━━${NC}"
    exit 1
else
    echo -e "${GREEN}━━━ Pre-commit PASSED ━━━${NC}"
    exit 0
fi
