#!/usr/bin/env bash
# =============================================================================
# Unified Test Runner — Meeting Co-Pilot
#
# Runs any combination of test suites and produces JSON results that the
# report generator (scripts/generate_test_report.py) reads to build a
# combined Markdown report.
#
# Usage:
#   ./scripts/run_all_tests.sh                      # Run ALL suites
#   ./scripts/run_all_tests.sh --suite backend       # Only backend pytest (unit + integration)
#   ./scripts/run_all_tests.sh --suite unit          # Only backend unit tests
#   ./scripts/run_all_tests.sh --suite integration   # Only backend integration tests
#   ./scripts/run_all_tests.sh --suite security      # Only security regression tests
#   ./scripts/run_all_tests.sh --suite chaos         # Only chaos / fault-injection tests
#   ./scripts/run_all_tests.sh --suite contract      # Only contract / schema-drift tests
#   ./scripts/run_all_tests.sh --suite migration     # Only DB migration tests
#   ./scripts/run_all_tests.sh --suite llm_eval      # Only LLM evals (needs LLM_EVAL_LIVE=1)
#   ./scripts/run_all_tests.sh --suite e2e           # Only Playwright E2E
#   ./scripts/run_all_tests.sh --suite a11y          # Only accessibility tests
#   ./scripts/run_all_tests.sh --suite visual        # Only visual regression tests
#   ./scripts/run_all_tests.sh --suite load          # Only k6 load smoke
#   ./scripts/run_all_tests.sh --suite mutation       # Only mutation testing
#   ./scripts/run_all_tests.sh --suite synthetic      # Only synthetic prod monitoring
#   ./scripts/run_all_tests.sh --suite unit --suite e2e  # Multiple suites
#
# Options:
#   --suite <name>    Run specific suite(s). Omit to run all.
#   --report          Generate markdown report after tests (default: yes)
#   --no-report       Skip report generation
#   --verbose         Show full test output
#   --help            Show this help
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="$REPO_ROOT/test-reports"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"

# ── Auto-detect virtualenv ───────────────────────────────────────────────────
# Check common venv locations and activate if found, so pytest has access
# to project dependencies (fastapi, etc.).
VENV_ACTIVATED=false
for venv_candidate in "$REPO_ROOT/.venv" "$BACKEND_DIR/.venv" "$REPO_ROOT/venv" "$BACKEND_DIR/venv"; do
    if [ -f "$venv_candidate/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "$venv_candidate/bin/activate"
        VENV_ACTIVATED=true
        break
    fi
done
if [ "$VENV_ACTIVATED" = false ] && [ -n "${VIRTUAL_ENV:-}" ]; then
    VENV_ACTIVATED=true  # already activated by caller
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
SUITES=()
GENERATE_REPORT=true
VERBOSE=false

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            SUITES+=("$2")
            shift 2
            ;;
        --report)
            GENERATE_REPORT=true
            shift
            ;;
        --no-report)
            GENERATE_REPORT=false
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --help)
            head -30 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# If no suites specified, run all (excluding expensive opt-in ones)
ALL_SUITES=(unit integration security chaos contract migration e2e a11y visual)
if [ ${#SUITES[@]} -eq 0 ]; then
    SUITES=("${ALL_SUITES[@]}")
    echo -e "${CYAN}Running ALL default suites: ${SUITES[*]}${NC}"
else
    echo -e "${CYAN}Running suites: ${SUITES[*]}${NC}"
fi

# ── Ensure report directory ──────────────────────────────────────────────────
mkdir -p "$REPORT_DIR"

OVERALL_EXIT=0
RESULTS_JSON="$REPORT_DIR/suite_results.json"
echo "[]" > "$RESULTS_JSON"

# ── Helper: append suite result to JSON ──────────────────────────────────────
record_result() {
    local suite="$1"
    local exit_code="$2"
    local duration="$3"
    local passed="${4:-0}"
    local failed="${5:-0}"
    local skipped="${6:-0}"
    local errors="${7:-0}"
    local detail_file="${8:-}"

    local status="passed"
    if [ "$exit_code" -ne 0 ]; then
        status="failed"
    fi

    python3 -c "
import json, sys
results = json.load(open('$RESULTS_JSON'))
results.append({
    'suite': '$suite',
    'status': '$status',
    'exit_code': $exit_code,
    'duration_seconds': $duration,
    'passed': $passed,
    'failed': $failed,
    'skipped': $skipped,
    'errors': $errors,
    'detail_file': '$detail_file'
})
json.dump(results, open('$RESULTS_JSON', 'w'), indent=2)
"
}

# ── Helper: run a pytest suite ───────────────────────────────────────────────
run_pytest_suite() {
    local suite_name="$1"
    local pytest_args="$2"
    local report_subdir="$REPORT_DIR/$suite_name"
    mkdir -p "$report_subdir"

    echo -e "\n${BOLD}${BLUE}━━━ $suite_name ━━━${NC}"
    local start_time=$(date +%s)

    local exit_code=0
    local output_file="$report_subdir/output.txt"

    (
        cd "$BACKEND_DIR"
        eval "python -m pytest $pytest_args" \
            --json-report --json-report-file="$report_subdir/results.json" \
            --tb=short --no-header -q \
            2>&1
    ) > "$output_file" 2>&1 || exit_code=$?

    local end_time=$(date +%s)
    local duration=$((end_time - start_time))

    # Parse JSON report for counts
    local passed=0 failed=0 skipped=0 errors=0
    if [ -f "$report_subdir/results.json" ]; then
        passed=$(python3 -c "import json; d=json.load(open('$report_subdir/results.json')); print(d.get('summary',{}).get('passed',0))" 2>/dev/null || echo 0)
        failed=$(python3 -c "import json; d=json.load(open('$report_subdir/results.json')); print(d.get('summary',{}).get('failed',0))" 2>/dev/null || echo 0)
        skipped=$(python3 -c "import json; d=json.load(open('$report_subdir/results.json')); print(d.get('summary',{}).get('skipped',0) + d.get('summary',{}).get('deselected',0))" 2>/dev/null || echo 0)
        errors=$(python3 -c "import json; d=json.load(open('$report_subdir/results.json')); print(d.get('summary',{}).get('error',0))" 2>/dev/null || echo 0)
    fi

    if [ $exit_code -eq 0 ]; then
        echo -e "  ${GREEN}✓ PASSED${NC} (${passed} passed, ${skipped} skipped) [${duration}s]"
    else
        echo -e "  ${RED}✗ FAILED${NC} (${passed} passed, ${failed} failed, ${skipped} skipped) [${duration}s]"
        OVERALL_EXIT=1
        if [ "$VERBOSE" = true ]; then
            cat "$output_file"
        else
            # Show just failed test names
            grep -E "^FAILED|^ERROR" "$output_file" 2>/dev/null | head -20 || true
        fi
    fi

    record_result "$suite_name" "$exit_code" "$duration" "$passed" "$failed" "$skipped" "$errors" "$report_subdir/results.json"
}

# ── Helper: run a Playwright suite ───────────────────────────────────────────
run_playwright_suite() {
    local suite_name="$1"
    local extra_args="$2"
    local report_subdir="$REPORT_DIR/$suite_name"
    mkdir -p "$report_subdir"

    echo -e "\n${BOLD}${BLUE}━━━ $suite_name ━━━${NC}"
    local start_time=$(date +%s)

    local exit_code=0
    local output_file="$report_subdir/output.txt"

    (
        cd "$FRONTEND_DIR"
        npx playwright test $extra_args \
            --reporter=json 2>&1
    ) > "$output_file" 2>&1 || exit_code=$?

    local end_time=$(date +%s)
    local duration=$((end_time - start_time))

    # Parse Playwright JSON for counts
    local passed=0 failed=0 skipped=0
    local pw_json="$REPORT_DIR/playwright/results.json"
    if [ -f "$pw_json" ]; then
        passed=$(python3 -c "
import json
d=json.load(open('$pw_json'))
suites=d.get('suites',[])
count=0
def walk(s):
    global count
    for spec in s.get('specs',[]):
        for test in spec.get('tests',[]):
            for result in test.get('results',[]):
                if result.get('status')=='passed': count+=1
    for sub in s.get('suites',[]): walk(sub)
for s in suites: walk(s)
print(count)
" 2>/dev/null || echo 0)
        failed=$(python3 -c "
import json
d=json.load(open('$pw_json'))
suites=d.get('suites',[])
count=0
def walk(s):
    global count
    for spec in s.get('specs',[]):
        for test in spec.get('tests',[]):
            for result in test.get('results',[]):
                if result.get('status') in ('failed','timedOut'): count+=1
    for sub in s.get('suites',[]): walk(sub)
for s in suites: walk(s)
print(count)
" 2>/dev/null || echo 0)
        skipped=$(python3 -c "
import json
d=json.load(open('$pw_json'))
suites=d.get('suites',[])
count=0
def walk(s):
    global count
    for spec in s.get('specs',[]):
        for test in spec.get('tests',[]):
            for result in test.get('results',[]):
                if result.get('status')=='skipped': count+=1
    for sub in s.get('suites',[]): walk(sub)
for s in suites: walk(s)
print(count)
" 2>/dev/null || echo 0)
    fi

    if [ $exit_code -eq 0 ]; then
        echo -e "  ${GREEN}✓ PASSED${NC} (${passed} passed, ${skipped} skipped) [${duration}s]"
    else
        echo -e "  ${RED}✗ FAILED${NC} (${passed} passed, ${failed} failed) [${duration}s]"
        OVERALL_EXIT=1
        if [ "$VERBOSE" = true ]; then
            cat "$output_file"
        fi
    fi

    record_result "$suite_name" "$exit_code" "$duration" "$passed" "$failed" "$skipped" "0" "$pw_json"
}

# =============================================================================
# Suite Runners
# =============================================================================

for suite in "${SUITES[@]}"; do
    case "$suite" in

        # ── Backend: all (unit + integration, no special markers) ────────
        backend)
            run_pytest_suite "backend" "tests/ -m 'not llm_eval and not load'"
            ;;

        # ── Backend: unit only ───────────────────────────────────────────
        unit)
            run_pytest_suite "unit" "tests/unit -x"
            ;;

        # ── Backend: integration only ────────────────────────────────────
        integration)
            run_pytest_suite "integration" "tests/integration -m \"not security and not chaos and not contract\""
            ;;

        # ── Security regression ──────────────────────────────────────────
        security)
            run_pytest_suite "security" "tests/integration/test_security_regression.py -m security"
            ;;

        # ── Chaos / fault injection ──────────────────────────────────────
        chaos)
            run_pytest_suite "chaos" "tests/integration/test_chaos.py -m chaos"
            ;;

        # ── Contract / schema drift ──────────────────────────────────────
        contract)
            run_pytest_suite "contract" "tests/integration/test_contract_drift.py -m contract"
            ;;

        # ── DB migration ─────────────────────────────────────────────────
        migration)
            run_pytest_suite "migration" "tests/integration/test_migrations.py"
            ;;

        # ── LLM evals (opt-in) ───────────────────────────────────────────
        llm_eval)
            if [ "${LLM_EVAL_LIVE:-0}" != "1" ]; then
                echo -e "\n${BOLD}${BLUE}━━━ llm_eval ━━━${NC}"
                echo -e "  ${YELLOW}⊘ Skipped (set LLM_EVAL_LIVE=1 to run)${NC}"
                record_result "llm_eval" "0" "0" "0" "0" "0" "0" ""
            else
                run_pytest_suite "llm_eval" "tests/llm_evals -m llm_eval"
            fi
            ;;

        # ── Playwright E2E ───────────────────────────────────────────────
        e2e)
            run_playwright_suite "e2e" "--project=chromium"
            ;;

        # ── Accessibility ────────────────────────────────────────────────
        a11y)
            run_playwright_suite "a11y" "--project=a11y"
            ;;

        # ── Visual regression ────────────────────────────────────────────
        visual)
            run_playwright_suite "visual" "--project=visual"
            ;;

        # ── k6 load test ─────────────────────────────────────────────────
        load)
            echo -e "\n${BOLD}${BLUE}━━━ load ━━━${NC}"
            if command -v k6 &> /dev/null; then
                local_report_dir="$REPORT_DIR/load"
                mkdir -p "$local_report_dir"
                local start_time=$(date +%s)
                local exit_code=0

                k6 run \
                    -e WS_URL="${WS_URL:-ws://localhost:5167/ws/streaming-audio}" \
                    -e AUTH_TOKEN="${AUTH_TOKEN:-test-token}" \
                    --out json="$local_report_dir/k6.json" \
                    "$REPO_ROOT/tests/load/streaming_load.js" \
                    > "$local_report_dir/output.txt" 2>&1 || exit_code=$?

                local end_time=$(date +%s)
                local duration=$((end_time - start_time))

                if [ $exit_code -eq 0 ]; then
                    echo -e "  ${GREEN}✓ PASSED${NC} [${duration}s]"
                else
                    echo -e "  ${RED}✗ FAILED${NC} [${duration}s]"
                    OVERALL_EXIT=1
                fi
                record_result "load" "$exit_code" "$duration" "0" "0" "0" "0" "$local_report_dir/k6.json"
            else
                echo -e "  ${YELLOW}⊘ k6 not installed, skipping${NC}"
                record_result "load" "0" "0" "0" "0" "1" "0" ""
            fi
            ;;

        # ── Mutation testing (opt-in, slow) ──────────────────────────────
        mutation)
            echo -e "\n${BOLD}${BLUE}━━━ mutation ━━━${NC}"
            mut_report_dir="$REPORT_DIR/mutation"
            mkdir -p "$mut_report_dir"
            local_start=$(date +%s)
            local_exit=0

            (cd "$BACKEND_DIR" && python -m mutmut run --no-progress 2>&1) \
                > "$mut_report_dir/output.txt" 2>&1 || local_exit=$?

            # Extract mutmut results
            (cd "$BACKEND_DIR" && python -m mutmut results 2>/dev/null) \
                > "$mut_report_dir/results.txt" 2>&1 || true

            local_end=$(date +%s)
            local_duration=$((local_end - local_start))

            killed=$(grep -c "killed" "$mut_report_dir/results.txt" 2>/dev/null || echo 0)
            survived=$(grep -c "survived" "$mut_report_dir/results.txt" 2>/dev/null || echo 0)

            echo -e "  Killed: $killed  Survived: $survived  [${local_duration}s]"
            record_result "mutation" "$local_exit" "$local_duration" "$killed" "$survived" "0" "0" "$mut_report_dir/results.txt"
            ;;

        # ── Synthetic prod monitoring ────────────────────────────────────
        synthetic)
            echo -e "\n${BOLD}${BLUE}━━━ synthetic ━━━${NC}"
            syn_report_dir="$REPORT_DIR/synthetic"
            mkdir -p "$syn_report_dir"
            syn_start=$(date +%s)
            syn_exit=0

            (cd "$FRONTEND_DIR" && npx playwright test \
                --config playwright.synthetic.config.ts \
                --reporter=json 2>&1) \
                > "$syn_report_dir/output.txt" 2>&1 || syn_exit=$?

            syn_end=$(date +%s)
            syn_duration=$((syn_end - syn_start))

            if [ $syn_exit -eq 0 ]; then
                echo -e "  ${GREEN}✓ PASSED${NC} [${syn_duration}s]"
            else
                echo -e "  ${RED}✗ FAILED${NC} [${syn_duration}s]"
                OVERALL_EXIT=1
            fi
            record_result "synthetic" "$syn_exit" "$syn_duration" "0" "0" "0" "0" "$syn_report_dir/output.txt"
            ;;

        *)
            echo -e "\n${RED}Unknown suite: $suite${NC}"
            echo "Valid suites: unit integration backend security chaos contract migration llm_eval e2e a11y visual load mutation synthetic"
            exit 1
            ;;
    esac
done

# =============================================================================
# Generate Markdown Report
# =============================================================================
if [ "$GENERATE_REPORT" = true ]; then
    echo -e "\n${BOLD}${BLUE}━━━ Generating Test Report ━━━${NC}"
    python3 "$REPO_ROOT/scripts/generate_test_report.py"
    echo -e "  Report: ${CYAN}$REPORT_DIR/TEST_REPORT.md${NC}"
fi

# =============================================================================
# Final Summary
# =============================================================================
echo ""
if [ $OVERALL_EXIT -eq 0 ]; then
    echo -e "${GREEN}${BOLD}═══ ALL SUITES PASSED ═══${NC}"
else
    echo -e "${RED}${BOLD}═══ SOME SUITES FAILED ═══${NC}"
fi

exit $OVERALL_EXIT
