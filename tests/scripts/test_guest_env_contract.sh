#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUNNER="${COSIM_DIR}/scripts/run_cosim_tests.sh"
FIXTURE_DIR="$(mktemp -d /tmp/cosim-guest-env-contract.XXXXXX)"
trap 'rm -rf -- "$FIXTURE_DIR"' EXIT

# shellcheck disable=SC1091
source "${COSIM_DIR}/scripts/cosim_guest_env.sh"

fail() {
    echo "[FAIL] guest_env_contract: $*" >&2
    exit 1
}

assert_interrupt() {
    local prefix="$1"
    local expected="$2"
    local actual

    actual="$(cosim_guest_hsa_interrupt "$prefix")" || \
        fail "valid prefix was rejected: ${prefix:-<empty>}"
    [[ "$actual" == "$expected" ]] || \
        fail "prefix ${prefix:-<empty>} resolved to $actual instead of $expected"
}

assert_interrupt "" 0
assert_interrupt HSA_ENABLE_INTERRUPT=0 0
assert_interrupt HSA_ENABLE_INTERRUPT=1 1

for invalid in HSA_ENABLE_INTERRUPT=2 'FOO=1' 'HSA_ENABLE_INTERRUPT=1;id'; do
    if cosim_guest_hsa_interrupt "$invalid" >/dev/null 2>&1; then
        fail "unsafe or unsupported prefix was accepted: $invalid"
    fi
done

printf '\033[?2004l\r[COSIM_ENV] HSA_ENABLE_INTERRUPT=1\r\n' > \
    "${FIXTURE_DIR}/qemu.log"
actual="$(cosim_guest_hsa_interrupt_from_log "${FIXTURE_DIR}/qemu.log")" || \
    fail "ANSI/CR-prefixed effective environment marker was not parsed"
[[ "$actual" == 1 ]] || fail "effective environment log resolved to $actual"
printf 'no environment marker\n' > "${FIXTURE_DIR}/missing.log"
if cosim_guest_hsa_interrupt_from_log "${FIXTURE_DIR}/missing.log" >/dev/null; then
    fail "log without an effective environment marker was accepted"
fi

# These are literal source-code contract needles, not shell expressions.
# shellcheck disable=SC2016
grep -Fq 'source "${SCRIPT_DIR}/cosim_guest_env.sh"' "$RUNNER" || \
    fail "host runner does not source the shared Guest environment parser"
# shellcheck disable=SC2016
grep -Fq 'GUEST_HSA_ENABLE_INTERRUPT="$(cosim_guest_hsa_interrupt "$GUEST_TEST_PREFIX")"' \
    "$RUNNER" || fail "host runner does not resolve GUEST_TEST_PREFIX"
# shellcheck disable=SC2016
grep -Fq 'export HSA_ENABLE_INTERRUPT="${GUEST_HSA_ENABLE_INTERRUPT}"' "$RUNNER" || \
    fail "resolved HSA interrupt value is not embedded in the Guest script"
# shellcheck disable=SC2016
grep -Fq 'cosim_guest_hsa_interrupt_from_log "$SCREEN_LOG"' "$RUNNER" || \
    fail "runner matrix does not use the CR-safe effective environment parser"
# shellcheck disable=SC2016
grep -Fq '"${PATCH_DIR}/repo-status.txt"' "$RUNNER" || \
    fail "runner does not preserve top-level repository status"
# shellcheck disable=SC2016
grep -Fq '"${PATCH_DIR}/repo.patch"' "$RUNNER" || \
    fail "runner does not preserve the top-level binary diff"
# shellcheck disable=SC2016
grep -Fq '"${PATCH_DIR}/repo-untracked-files.tar"' "$RUNNER" || \
    fail "runner does not archive untracked top-level source"
grep -Fq 'runner_sha256=' "$RUNNER" || \
    fail "source snapshot does not identify the host runner"
grep -Fq 'guest_env_helper_sha256=' "$RUNNER" || \
    fail "source snapshot does not identify the Guest environment helper"

echo "[PASS] guest_env_contract"
