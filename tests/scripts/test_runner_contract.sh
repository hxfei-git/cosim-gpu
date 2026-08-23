#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FIXTURE_DIR="$(mktemp -d /tmp/cosim-runner-contract.XXXXXX)"
trap 'rm -rf -- "$FIXTURE_DIR"' EXIT

fail() {
    echo "[FAIL] runner_contract: $*" >&2
    exit 1
}

make_fixture() {
    local name="$1"
    local body="$2"
    printf '#!/bin/bash\n%s\n' "$body" > "${FIXTURE_DIR}/${name}"
    chmod +x "${FIXTURE_DIR}/${name}"
}

make_fixture good 'echo "Timing: 1.0 ms"; echo "[PASS] good"; exit 0'
make_fixture no_marker 'exit 0'
make_fixture false_pass 'echo "[PASS] false_pass"; exit 1'
make_fixture hang_after_pass 'echo "[PASS] hang_after_pass"; sleep 10'

TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" good >/dev/null || fail "valid run did not pass"

if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" no_marker >/dev/null; then
    fail "exit zero without a PASS marker was accepted"
fi
if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" false_pass >/dev/null; then
    fail "nonzero exit with a PASS marker was accepted"
fi
if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=1 \
    "${COSIM_DIR}/tests/run_tests.sh" hang_after_pass >/dev/null; then
    fail "timeout after a PASS marker was accepted"
fi

json_output="$(TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" --json good)"
python3 -c 'import json, sys; data=json.load(sys.stdin); assert data["passed"] == 1' \
    <<<"$json_output" || fail "--json output was not standalone valid JSON"

if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=invalid \
    "${COSIM_DIR}/tests/run_tests.sh" good >/dev/null 2>&1; then
    fail "invalid timeout was accepted"
fi
if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" goo >/dev/null 2>&1; then
    fail "substring filter was accepted"
fi

echo "[PASS] runner_contract"
