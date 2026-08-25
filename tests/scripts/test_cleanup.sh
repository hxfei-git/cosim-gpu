#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck disable=SC1091
source "${COSIM_DIR}/scripts/cosim_lib.sh"

fail() {
    echo "[FAIL] cleanup_contract: $*" >&2
    exit 1
}

RUN_ID="unit-cleanup-${BASHPID}-${RANDOM}"
SESSION_DIR="/tmp/cosim-${RUN_ID}.session"
MARKER="/tmp/cosim-launcher-category-${RUN_ID}.txt"
CATEGORY_DIR="/tmp/cosim-runner-category-${RUN_ID}"
CONTAINER="$(cosim_container_name "$RUN_ID")"

[[ ! -e "$SESSION_DIR" && ! -e "$MARKER" && ! -e "$CATEGORY_DIR" ]] || \
    fail "test paths already exist"
manifest_init "$SESSION_DIR" "$RUN_ID" "$COSIM_DIR"
manifest_add runtime container "$CONTAINER"
manifest_add runtime file "$MARKER"
manifest_add runtime directory "$SESSION_DIR"
printf '%s\n' test > "$MARKER"
mkdir "$CATEGORY_DIR"

printf '%s\n' "$COSIM_CAT_TEST_PASS" > "${CATEGORY_DIR}/runner-category.txt"
[[ "$(cosim_recorded_runner_category "$CATEGORY_DIR")" == \
   "$COSIM_CAT_TEST_PASS" ]] || fail "runner success category was not preserved"
printf '%s\n' unknown > "${CATEGORY_DIR}/runner-category.txt"
if cosim_recorded_runner_category "$CATEGORY_DIR" >/dev/null; then
    fail "unknown runner category was accepted"
fi
printf '%s\n' "$COSIM_CAT_TEST_PASS" "$COSIM_CAT_TEST_FAIL" > \
    "${CATEGORY_DIR}/runner-category.txt"
if cosim_recorded_runner_category "$CATEGORY_DIR" >/dev/null; then
    fail "multi-line runner category was accepted"
fi
rm -f -- "${CATEGORY_DIR}/runner-category.txt"
ln -s /etc/passwd "${CATEGORY_DIR}/runner-category.txt"
if cosim_recorded_runner_category "$CATEGORY_DIR" >/dev/null; then
    fail "symlinked runner category was accepted"
fi
rm -f -- "${CATEGORY_DIR}/runner-category.txt"
rmdir -- "$CATEGORY_DIR"

"${COSIM_DIR}/scripts/cosim_cleanup.sh" --run-id "$RUN_ID" \
    --manifest "${SESSION_DIR}/resources.manifest" >/dev/null
[[ -e "$SESSION_DIR" && -e "$MARKER" ]] || \
    fail "dry-run changed runtime files"

cleanup_from_manifest "$CONTAINER"
verify_cleanup 1 "$CONTAINER" || fail "verified cleanup reported leftovers"
[[ ! -e "$SESSION_DIR" && ! -e "$MARKER" ]] || \
    fail "owned paths were not removed"

BAD_RUN_ID="unit-cleanup-bad-${BASHPID}-${RANDOM}"
BAD_SESSION="/tmp/cosim-${BAD_RUN_ID}.session"
BAD_CONTAINER="$(cosim_container_name "$BAD_RUN_ID")"
manifest_init "$BAD_SESSION" "$BAD_RUN_ID" "$COSIM_DIR"
manifest_add runtime container "$BAD_CONTAINER"
manifest_add runtime file "/tmp/../etc"
manifest_add runtime directory "$BAD_SESSION"
if cleanup_from_manifest "$BAD_CONTAINER" >/dev/null 2>&1; then
    fail "unsafe manifest entry was accepted"
fi
[[ -e /etc ]] || fail "unsafe target was modified"

echo "[PASS] cleanup_contract"
