#!/bin/bash
# Test runner for cosim GPU operator tests.
# Usage:
#   ./run_tests.sh              # run all tests
#   ./run_tests.sh gemm         # run only matching tests
#   ./run_tests.sh --json       # JSON output for CI
#
# Exit code: 0 if all tests pass, 1 if any test fails.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${TEST_BUILD_DIR:-${SCRIPT_DIR}/build}"
FILTER=""
JSON=0
TEST_TIMEOUT_SECS="${TEST_TIMEOUT_SECS:-60}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)  JSON=1; shift ;;
        *)
            [[ -z "$FILTER" ]] || {
                echo "Only one exact test name may be supplied." >&2
                exit 2
            }
            FILTER="$1"
            shift
            ;;
    esac
done

[[ "$TEST_TIMEOUT_SECS" =~ ^[1-9][0-9]*$ ]] || {
    echo "TEST_TIMEOUT_SECS must be a positive integer." >&2
    exit 2
}
[[ -z "$FILTER" || "$FILTER" =~ ^[a-z0-9_]+$ ]] || {
    echo "Invalid test name: $FILTER" >&2
    exit 2
}

if [[ ! -d "$BUILD_DIR" ]]; then
    echo "Build directory not found. Run 'make' first."
    exit 2
fi

TESTS=()
for bin in "$BUILD_DIR"/*; do
    [[ -x "$bin" ]] || continue
    name="$(basename "$bin")"
    if [[ -n "$FILTER" && "$name" != "$FILTER" ]]; then
        continue
    fi
    TESTS+=("$bin")
done

if [[ ${#TESTS[@]} -eq 0 ]]; then
    echo "No tests found (filter: '${FILTER:-<none>}')."
    exit 2
fi

PASSED=0
FAILED=0
TOTAL=${#TESTS[@]}
RESULTS=()

run_with_timeout() {
    local timeout_secs="$1"
    local output_file="$2"
    shift 2

    local start_ts
    local now
    local pid

    "$@" </dev/null >"$output_file" 2>&1 &
    pid=$!
    start_ts=$(date +%s)

    while kill -0 "$pid" 2>/dev/null; do
        now=$(date +%s)
        if (( now - start_ts >= timeout_secs )); then
            kill -TERM "$pid" 2>/dev/null || true
            sleep 1
            kill -KILL "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 1
    done

    wait "$pid"
}

if [[ $JSON -eq 0 ]]; then
    echo "============================================================"
    echo "  cosim GPU Operator Tests"
    echo "  Tests: $TOTAL"
    echo "============================================================"
    echo ""
fi

for test_bin in "${TESTS[@]}"; do
    name="$(basename "$test_bin")"
    tmp_output="$(mktemp)"

    if [[ $JSON -eq 0 ]]; then
        echo "[RUN] $name (timeout: ${TEST_TIMEOUT_SECS}s)"
    fi

    if run_with_timeout "${TEST_TIMEOUT_SECS}" "$tmp_output" "$test_bin"; then
        rc=0
    else
        rc=$?
    fi
    output="$(cat "$tmp_output")"
    rm -f "$tmp_output"

    if [[ "$rc" -eq 0 ]]; then
        pass_count="$(awk -v marker="[PASS] ${name}" \
            '$0 == marker {count++} END {print count + 0}' <<<"$output")"
        fail_count="$(awk '/^\[FAIL\] / {count++} END {print count + 0}' <<<"$output")"
        if [[ "$pass_count" -ne 1 || "$fail_count" -ne 0 ]]; then
            rc=1
        fi
    fi

    case "$rc" in
        0)
            status="PASS"
            PASSED=$((PASSED + 1))
            ;;
        124)
            status="TIMEOUT"
            FAILED=$((FAILED + 1))
            ;;
        *)
            status="FAIL"
            FAILED=$((FAILED + 1))
            ;;
    esac

    # Extract the first standard timing line.
    ms="$(sed -n 's/^Timing: \([0-9.][0-9.]*\) ms$/\1/p' <<<"$output" | head -n 1)"
    ms=${ms:-"?"}

    if [[ $JSON -eq 0 ]]; then
        echo "$output"
        if [[ $status == "TIMEOUT" ]]; then
            echo "[TIMEOUT] $name exceeded ${TEST_TIMEOUT_SECS}s"
        fi
        echo ""
    fi

    RESULTS+=("{\"name\":\"$name\",\"status\":\"$status\",\"time_ms\":\"$ms\"}")
done

if [[ $JSON -eq 1 ]]; then
    echo "{"
    echo "  \"total\": $TOTAL,"
    echo "  \"passed\": $PASSED,"
    echo "  \"failed\": $FAILED,"
    echo "  \"tests\": ["
    for i in "${!RESULTS[@]}"; do
        comma=","
        [[ $i -eq $((${#RESULTS[@]} - 1)) ]] && comma=""
        echo "    ${RESULTS[$i]}${comma}"
    done
    echo "  ]"
    echo "}"
else
    echo "============================================================"
    echo "  Results: ${PASSED}/${TOTAL} passed, ${FAILED} failed"
    echo "============================================================"
fi

[[ $FAILED -eq 0 ]]
