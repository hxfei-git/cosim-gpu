#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LAUNCHER="${COSIM_DIR}/scripts/cosim_launch.sh"

fail() {
    echo "[FAIL] guest_disk_overlay_contract: $*" >&2
    exit 1
}

PATH_FIXTURE="$(mktemp -d /tmp/cosim-qemu-path-contract.XXXXXX)"
trap 'rm -rf -- "$PATH_FIXTURE"' EXIT

# shellcheck disable=SC1091
source "${COSIM_DIR}/scripts/cosim_lib.sh"

# launcher 必须在任何检查、归档或执行前，把 QEMU 与 qemu-img 统一为物理绝对路径。
# shellcheck disable=SC2016
grep -Fq 'QEMU_BIN="$(realpath -e -- "$REQUESTED_QEMU_BIN"' "$LAUNCHER" || \
    fail "launcher 未 canonicalize QEMU_BIN"
# shellcheck disable=SC2016
grep -Fq 'QEMU_IMG="$(realpath -e -- "$REQUESTED_QEMU_IMG"' "$LAUNCHER" || \
    fail "launcher 未 canonicalize QEMU_IMG"
# shellcheck disable=SC2016
qemu_bin_realpath_line="$(grep -nF 'QEMU_BIN="$(realpath -e -- "$REQUESTED_QEMU_BIN"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_img_realpath_line="$(grep -nF 'QEMU_IMG="$(realpath -e -- "$REQUESTED_QEMU_IMG"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_bin_check_line="$(grep -nF '[[ -f "$QEMU_BIN" && -x "$QEMU_BIN"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_img_check_line="$(grep -nF '[[ -f "$QEMU_IMG" && -x "$QEMU_IMG"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_archive_line="$(grep -nF 'echo "qemu_binary=${QEMU_BIN}"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_img_archive_line="$(grep -nF 'echo "qemu_img=${QEMU_IMG}"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_img_execute_line="$(grep -nF '"$QEMU_IMG" create -q' \
    "$LAUNCHER" | cut -d: -f1)"
qemu_execute_line="$(grep -nF 'QEMU_CMD=(' "$LAUNCHER" | cut -d: -f1)"
[[ "$qemu_bin_realpath_line" -lt "$qemu_bin_check_line" && \
   "$qemu_bin_check_line" -lt "$qemu_archive_line" && \
   "$qemu_archive_line" -lt "$qemu_execute_line" && \
   "$qemu_img_realpath_line" -lt "$qemu_img_check_line" && \
   "$qemu_img_check_line" -lt "$qemu_img_archive_line" && \
   "$qemu_img_archive_line" -lt "$qemu_img_execute_line" ]] || \
    fail "QEMU 路径 canonicalization 未发生在检查、归档和执行之前"

canonical_bin_dir="${PATH_FIXTURE}/canonical/bin"
mkdir -p "$canonical_bin_dir"
printf '#!/bin/sh\nexit 0\n' > "${canonical_bin_dir}/qemu-system-x86_64"
printf '#!/bin/sh\nexit 0\n' > "${canonical_bin_dir}/qemu-img"
chmod +x "${canonical_bin_dir}/qemu-system-x86_64" \
    "${canonical_bin_dir}/qemu-img"
ln -s "${canonical_bin_dir}/qemu-system-x86_64" \
    "${PATH_FIXTURE}/qemu-system-alias"
ln -s "${canonical_bin_dir}/qemu-img" "${PATH_FIXTURE}/qemu-img-alias"
ln -s "$canonical_bin_dir" "${PATH_FIXTURE}/parent-bin-alias"
canonical_qemu="$(realpath -e -- "${canonical_bin_dir}/qemu-system-x86_64")"
canonical_qemu_img="$(realpath -e -- "${canonical_bin_dir}/qemu-img")"
[[ "$(realpath -e -- "${PATH_FIXTURE}/qemu-system-alias")" == \
   "$canonical_qemu" ]] || fail "QEMU final symlink alias 未解析到 canonical binary"
[[ "$(realpath -e -- "${PATH_FIXTURE}/qemu-img-alias")" == \
   "$canonical_qemu_img" ]] || fail "qemu-img final symlink alias 未解析到 canonical binary"
[[ "$(cd "$PATH_FIXTURE" && realpath -e -- \
        'canonical/bin/../bin/qemu-system-x86_64')" == "$canonical_qemu" ]] || \
    fail "QEMU 相对/.. 路径未解析到 canonical binary"
[[ "$(cd "$PATH_FIXTURE" && realpath -e -- \
        'canonical/bin/../bin/qemu-img')" == "$canonical_qemu_img" ]] || \
    fail "qemu-img 相对/.. 路径未解析到 canonical binary"
[[ "$(realpath -e -- "${PATH_FIXTURE}/parent-bin-alias/qemu-system-x86_64")" == \
   "$canonical_qemu" ]] || fail "QEMU 父目录 symlink alias 未解析到 canonical binary"
[[ "$(realpath -e -- "${PATH_FIXTURE}/parent-bin-alias/qemu-img")" == \
   "$canonical_qemu_img" ]] || fail "qemu-img 父目录 symlink alias 未解析到 canonical binary"

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
grep -Fq 'flock -s 9' "$LAUNCHER" || \
    fail "launcher does not hold the shared build lock"
for evidence in guest-provenance.json guest-content-seal.txt guest.lock \
    guest-overlay.patch guest-base-stat-pre.json guest-base-stat-post.json \
    qemu-build-meta.txt toolchain.lock; do
    grep -Fq "$evidence" "$LAUNCHER" || \
        fail "launcher does not preserve ${evidence}"
done
# shellcheck disable=SC2016
grep -Fq 'archive_strict_evidence_file "$QEMU_BUILD_META"' "$LAUNCHER" || \
    fail "launcher 未归档 preflight 已验证的 QEMU build metadata"
# shellcheck disable=SC2016
grep -Fq 'archive_strict_evidence_file "$TOOLCHAIN_LOCK"' "$LAUNCHER" || \
    fail "launcher 未归档 preflight 已验证的 QEMU toolchain lock"
grep -Fq 'qemu_meta_sha' "$LAUNCHER" || \
    fail "launcher 未复核 QEMU build metadata hash"
grep -Fq 'toolchain_lock_sha' "$LAUNCHER" || \
    fail "launcher 未复核 QEMU toolchain lock hash"
[[ "$(grep -Fc 'jq -nre --arg detail' "$LAUNCHER")" -eq 2 ]] || \
    fail "launcher 的 QEMU provenance hash 提取未使用 fail-closed jq"
grep -Fq 'image_mtime_ns' "${COSIM_DIR}/scripts/guest_provenance.py" || \
    fail "Guest content seal omits nanosecond mtime"
grep -Fq 'image_ctime_ns' "${COSIM_DIR}/scripts/guest_provenance.py" || \
    fail "Guest content seal omits nanosecond ctime"

runtime_path_is_safe test-overlay file \
    /tmp/cosim-test-overlay.session/guest-overlay.qcow2 || \
    fail "cleanup policy rejects the scoped overlay"
if runtime_path_is_safe test-overlay file /tmp/guest-overlay.qcow2; then
    fail "cleanup policy accepts an overlay without the run ID"
fi

echo "[PASS] guest_disk_overlay_contract"
