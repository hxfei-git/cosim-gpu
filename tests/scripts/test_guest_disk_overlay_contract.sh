#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LAUNCHER="${COSIM_DIR}/scripts/cosim_launch.sh"

fail() {
    echo "[FAIL] guest_disk_overlay_contract: $*" >&2
    exit 1
}

# shellcheck disable=SC1091
source "${COSIM_DIR}/scripts/cosim_lib.sh"

# These are literal source-code contract needles, not shell expressions.
# shellcheck disable=SC2016
grep -Fq 'GUEST_OVERLAY="${SESSION_DIR}/guest-overlay.qcow2"' "$LAUNCHER" || \
    fail "launcher does not use a run-scoped Guest overlay"
# shellcheck disable=SC2016
grep -Fq 'manifest_add "runtime" "file" "$GUEST_OVERLAY"' "$LAUNCHER" || \
    fail "Guest overlay is not owned by the cleanup manifest"
# shellcheck disable=SC2016
grep -Fq '"$QEMU_IMG" create -q -f qcow2 -F raw -b "$DISK_IMAGE" "$GUEST_OVERLAY"' \
    "$LAUNCHER" || fail "launcher does not create a raw-backed qcow2 overlay"
# shellcheck disable=SC2016
grep -Fq -- '-drive "file=$GUEST_OVERLAY,format=qcow2,if=virtio"' "$LAUNCHER" || \
    fail "QEMU does not boot from the run-scoped overlay"
# shellcheck disable=SC2016
if grep -Fq -- '-drive "file=$DISK_IMAGE,format=raw,if=virtio"' "$LAUNCHER"; then
    fail "launcher still exposes the built raw base as a writable Guest drive"
fi
grep -Fq 'guest-overlay.json' "$LAUNCHER" || \
    fail "launcher does not preserve overlay provenance"
grep -Fq 'guest-build-meta.txt' "$LAUNCHER" || \
    fail "launcher does not preserve Guest build provenance"

runtime_path_is_safe test-overlay file \
    /tmp/cosim-test-overlay.session/guest-overlay.qcow2 || \
    fail "cleanup policy rejects the scoped overlay"
if runtime_path_is_safe test-overlay file /tmp/guest-overlay.qcow2; then
    fail "cleanup policy accepts an overlay without the run ID"
fi

echo "[PASS] guest_disk_overlay_contract"
