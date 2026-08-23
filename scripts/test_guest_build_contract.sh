#!/bin/bash
# Fast, offline checks for the staged and pinned GPU guest image build.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_SCRIPT="${SCRIPT_DIR}/cosim_build.sh"
RUN_SCRIPT="${SCRIPT_DIR}/run_mi300x_fs.sh"
GUEST_LOCK="${COSIM_DIR}/configs/cosim/guest.lock"
GUEST_PATCH="${SCRIPT_DIR}/patches/0002-guest-core-reproducible.patch"
RESOURCES_DIR="${COSIM_DIR}/gem5-resources"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

assert_contains() {
    local needle="$1"
    local file="$2"
    grep -F -- "$needle" "$file" >/dev/null || \
        fail "missing contract text '$needle' in $file"
}

bash -n "$BUILD_SCRIPT" "$RUN_SCRIPT"
assert_contains 'guest) build_guest ;;' "$BUILD_SCRIPT"
assert_contains 'all) build_guest ;;' "$BUILD_SCRIPT"
assert_contains 'PACKER_PLUGIN_PATH=' "$BUILD_SCRIPT"
assert_contains 'validate_guest_image' "$BUILD_SCRIPT"
assert_contains 'validate_guest_kernel' "$BUILD_SCRIPT"
assert_contains 'guest_metadata_matches' "$BUILD_SCRIPT"
assert_contains 'installer-serial.log' "$BUILD_SCRIPT"
# Contract checks require literal HCL/shell interpolation.
# shellcheck disable=SC2016
assert_contains '-var "serial_log=${artifact_dir}/installer-serial.log"' "$BUILD_SCRIPT"
assert_contains 'COSIM_GUEST_BUILD_TIMEOUT:-4h' "$BUILD_SCRIPT"
assert_contains 'timeout --signal=INT --kill-after=2m --foreground' "$BUILD_SCRIPT"
assert_contains 'classification="autoinstall_error"' "$BUILD_SCRIPT"
assert_contains 'classification="host_timeout"' "$BUILD_SCRIPT"
assert_contains 'classification=post_packer_image_validation_failure' "$BUILD_SCRIPT"
assert_contains 'classification=post_packer_kernel_validation_failure' "$BUILD_SCRIPT"
assert_contains 'tee_exit_code=' "$BUILD_SCRIPT"
assert_contains 'attempt-status.txt' "$BUILD_SCRIPT"
assert_contains 'for log_name in installer-serial.log packer.log console.log' "$BUILD_SCRIPT"
assert_contains 'install -m 0600 /dev/null' "$BUILD_SCRIPT"
assert_contains "\"\${SCRIPT_DIR}/cosim_build.sh\" guest" "$RUN_SCRIPT"
if grep -F './build.sh -var' "$RUN_SCRIPT" >/dev/null; then
    fail "run_mi300x_fs.sh bypasses the reproducible guest build action"
fi
if grep -F 'read -p "Rebuild?' "$RUN_SCRIPT" >/dev/null; then
    fail "guest build path still contains an interactive rebuild prompt"
fi

assert_contains 'PACKER_VERSION=1.10.0' "$GUEST_LOCK"
assert_contains 'PACKER_SHA256=a8442e7041db0a7db48f468e353ee07fa6a7b35276ec62f60813c518ca3296c1' "$GUEST_LOCK"
assert_contains 'PACKER_QEMU_PLUGIN_VERSION=1.1.6' "$GUEST_LOCK"
assert_contains 'PACKER_QEMU_PLUGIN_SHA256=3f735539fbdd0368785babda272b85738866f736415dce59d04b4cb550c4db87' "$GUEST_LOCK"
assert_contains 'UBUNTU_ISO_SHA256=d6dab0c3a657988501b4bd76f1297c053df710e06e0c3aece60dead24f270b4d' "$GUEST_LOCK"
assert_contains 'AMDGPU_DKMS_VERSION=1:6.14.14.30100000-2204008.24.04' "$GUEST_LOCK"
assert_contains 'ROCM_VERSION=7.0.0.70000-38~24.04' "$GUEST_LOCK"
assert_contains 'GUEST_KERNEL=6.8.0-79-generic' "$GUEST_LOCK"
assert_contains 'GUEST_KERNEL_IMAGE_DEB_SHA256=24d948462eadec3309354f803fe8b7f5f14441498dcdd055044d579b65c83b5e' "$GUEST_LOCK"
assert_contains 'GUEST_KERNEL_MODULES_DEB_SHA256=bc0031fcd160dc3cbb50734e1e173223a8fe29d006c17b9f28a6aa45f19d4e5b' "$GUEST_LOCK"
assert_contains 'GUEST_KERNEL_MODULES_EXTRA_DEB_SHA256=95802c55ad41be81e6511ff044d77f1528cd81ab4cf626e30714d39d6d64a22a' "$GUEST_LOCK"
assert_contains 'GUEST_KERNEL_HEADERS_DEB_SHA256=80c1e08da84f88c8ca080a4fa513a879556b3e65d634bf760c32e6a1e092186d' "$GUEST_LOCK"
assert_contains 'GUEST_KERNEL_HEADERS_GENERIC_DEB_SHA256=54cb0dfd1564d57ef13728ca9efe7dedef65e54a3538f8c06f12c356d8801da2' "$GUEST_LOCK"
assert_contains 'prepare_guest_kernel_debs' "$BUILD_SCRIPT"

TEST_CONTEXT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_CONTEXT"' EXIT
git -C "$RESOURCES_DIR" archive HEAD src/x86-ubuntu-gpu-ml |
    tar -x -C "$TEST_CONTEXT"
patch --directory="$TEST_CONTEXT" --strip=1 --fuzz=0 --batch \
    --input="$GUEST_PATCH" >/dev/null

ROCM_SCRIPT="${TEST_CONTEXT}/src/x86-ubuntu-gpu-ml/scripts/rocm-install.sh"
PACKER_TEMPLATE="${TEST_CONTEXT}/src/x86-ubuntu-gpu-ml/x86-ubuntu-gpu-ml.pkr.hcl"
bash -n "$ROCM_SCRIPT"
command -v shellcheck >/dev/null 2>&1 && shellcheck "$ROCM_SCRIPT"
assert_contains 'set -euo pipefail' "$ROCM_SCRIPT"
assert_contains "amdgpu-dkms=\${AMDGPU_DKMS_VERSION}" "$ROCM_SCRIPT"
assert_contains "rocm=\${ROCM_VERSION}" "$ROCM_SCRIPT"
assert_contains 'KERNEL_DEB_DIR="/home/gem5/kernel-debs"' "$ROCM_SCRIPT"
# Contract checks require literal shell interpolation.
# shellcheck disable=SC2016
assert_contains 'sudo apt-get install -y "${kernel_debs[@]}"' "$ROCM_SCRIPT"
assert_contains 'version = "= 1.1.6"' "$PACKER_TEMPLATE"
assert_contains 'old-releases.ubuntu.com/releases/24.04.2' "$PACKER_TEMPLATE"
assert_contains 'sensitive = true' "$PACKER_TEMPLATE"
assert_contains 'console=ttyS0,115200n8' "$PACKER_TEMPLATE"
# Contract checks require literal HCL interpolation.
# shellcheck disable=SC2016
assert_contains '["-serial", "file:${var.serial_log}"]' "$PACKER_TEMPLATE"
assert_contains 'source      = "files/kernel-debs/"' "$PACKER_TEMPLATE"

USER_DATA="${TEST_CONTEXT}/src/x86-ubuntu-gpu-ml/http/user-data"
assert_contains 'shutdown: reboot' "$USER_DATA"
assert_contains 'COSIM_AUTOINSTALL_START' "$USER_DATA"
assert_contains 'COSIM_AUTOINSTALL_COMPLETE' "$USER_DATA"
assert_contains 'COSIM_AUTOINSTALL_ERROR' "$USER_DATA"
assert_contains 'reporting:' "$USER_DATA"
if grep -F 'journalctl' "$USER_DATA" >/dev/null; then
    fail "guest installer telemetry may expose broad journal contents"
fi
if grep -F 'pip3 install' "$ROCM_SCRIPT" >/dev/null; then
    fail "unversioned PyTorch installation remains in the driver/HIP baseline"
fi
# Contract checks require literal shell interpolation.
# shellcheck disable=SC2016
if grep -F 'apt -y install "linux-image-${KERNEL}"' "$ROCM_SCRIPT" >/dev/null; then
    fail "guest kernel installation still depends on the rolling Ubuntu index"
fi

echo "[PASS] guest build contract"
