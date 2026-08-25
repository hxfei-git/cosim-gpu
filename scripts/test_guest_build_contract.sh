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

assert_equals() {
    local expected="$1"
    local actual="$2"
    local label="$3"

    [[ "$actual" == "$expected" ]] || \
        fail "${label}：预期 '${expected}'，实际 '${actual}'"
}

assert_contains() {
    local needle="$1"
    local file="$2"
    grep -F -- "$needle" "$file" >/dev/null || \
        fail "missing contract text '$needle' in $file"
}

SHELLCHECK_BIN="$(command -v shellcheck)" || \
    fail "缺少必需命令：shellcheck"
bash -n "$BUILD_SCRIPT" "$RUN_SCRIPT"
"$SHELLCHECK_BIN" "$BUILD_SCRIPT"
assert_contains 'guest) build_guest ;;' "$BUILD_SCRIPT"
assert_contains 'all) build_guest ;;' "$BUILD_SCRIPT"
assert_contains 'PACKER_PLUGIN_PATH=' "$BUILD_SCRIPT"
assert_contains 'validate_guest_image' "$BUILD_SCRIPT"
assert_contains 'validate_guest_kernel' "$BUILD_SCRIPT"
assert_contains 'guest_metadata_matches' "$BUILD_SCRIPT"
assert_contains "GUEST_SEAL=\"\${GUEST_BUILD_ROOT}/.cosim-content-seal\"" "$BUILD_SCRIPT"
assert_contains "guest_metadata_reseal \"\$recipe_fingerprint\"" "$BUILD_SCRIPT"
assert_contains "guest_provenance seal --known-image-sha256 \"\$image_sha\"" "$BUILD_SCRIPT"
assert_contains "cp \"\$GUEST_SEAL\" \"\${artifact_dir}/content-seal.txt\"" "$BUILD_SCRIPT"
assert_contains 'installer-serial.log' "$BUILD_SCRIPT"
# Contract checks require literal HCL/shell interpolation.
# shellcheck disable=SC2016
assert_contains '-var "serial_log=${artifact_dir}/installer-serial.log"' "$BUILD_SCRIPT"
assert_contains 'COSIM_GUEST_BUILD_TIMEOUT:-4h' "$BUILD_SCRIPT"
assert_contains 'timeout --signal=INT --kill-after=2m --foreground' "$BUILD_SCRIPT"
assert_contains 'GUEST_FAILURE_CLASSIFICATION="autoinstall_error"' "$BUILD_SCRIPT"
assert_contains 'GUEST_FAILURE_CLASSIFICATION="host_timeout"' "$BUILD_SCRIPT"
assert_contains 'GUEST_FAILURE_CLASSIFICATION="external_interrupt"' "$BUILD_SCRIPT"
assert_contains 'guest_network_tls_failure' "$BUILD_SCRIPT"
assert_contains 'guest_network_timeout' "$BUILD_SCRIPT"
assert_contains 'post_packer_image_validation_failure' "$BUILD_SCRIPT"
assert_contains 'post_packer_kernel_validation_failure' "$BUILD_SCRIPT"
assert_contains 'secondary_classification=' "$BUILD_SCRIPT"
assert_contains 'packer_init_exit_code=' "$BUILD_SCRIPT"
assert_contains 'packer_validate_exit_code=' "$BUILD_SCRIPT"
assert_contains 'packer_build_exit_code=' "$BUILD_SCRIPT"
assert_contains 'build_pipeline_exit_code=' "$BUILD_SCRIPT"
assert_contains 'tee_exit_code=' "$BUILD_SCRIPT"
assert_contains 'packer_exit_code=' "$BUILD_SCRIPT"
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

build_guest_body="$(sed -n '/^build_guest() {/,/^}/p' "$BUILD_SCRIPT")"
reseal_line="$(grep -n -F "guest_metadata_reseal \"\$recipe_fingerprint\"" \
    <<< "$build_guest_body" | cut -d: -f1)"
packer_line="$(grep -n -F 'prepare_packer_toolchain' \
    <<< "$build_guest_body" | cut -d: -f1)"
[[ -n "$reseal_line" && -n "$packer_line" && "$reseal_line" -lt "$packer_line" ]] || \
    fail "legacy Guest reseal does not precede all Packer preparation"
if sed -n '/^guest_metadata_matches() {/,/^}/p' "$BUILD_SCRIPT" | \
    grep -F "sha256sum \"\$GUEST_IMAGE\"" >/dev/null; then
    fail "normal Guest metadata validation still fully hashes the base image"
fi

TEST_CONTEXT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_CONTEXT"' EXIT

# 仅加载函数定义；cosim_build.sh 使用 BASH_SOURCE 保护主入口。
# 这样可在不启动构建的情况下覆盖故障分类行为。
# shellcheck source=cosim_build.sh
# shellcheck disable=SC1090,SC1091
source "$BUILD_SCRIPT"
CLASSIFIER_CONTEXT="${TEST_CONTEXT}/classifier"
mkdir -p "$CLASSIFIER_CONTEXT"

assert_failure_classification() {
    local expected_primary="$1"
    local expected_secondary="$2"
    local init_rc="$3"
    local validate_rc="$4"
    local packer_build_rc="$5"
    local build_pipeline_rc="$6"
    local tee_rc="$7"
    local installer_text="$8"
    local console_text="$9"
    local packer_text="${10}"

    printf '%s\n' "$installer_text" > "${CLASSIFIER_CONTEXT}/installer.log"
    printf '%s\n' "$console_text" > "${CLASSIFIER_CONTEXT}/console.log"
    printf '%s\n' "$packer_text" > "${CLASSIFIER_CONTEXT}/packer.log"
    classify_guest_build_failure \
        "$init_rc" "$validate_rc" "$packer_build_rc" \
        "$build_pipeline_rc" "$tee_rc" \
        "${CLASSIFIER_CONTEXT}/installer.log" \
        "${CLASSIFIER_CONTEXT}/console.log" \
        "${CLASSIFIER_CONTEXT}/packer.log"
    assert_equals "$expected_primary" "$GUEST_FAILURE_CLASSIFICATION" \
        "主故障分类"
    assert_equals "$expected_secondary" \
        "$GUEST_FAILURE_SECONDARY_CLASSIFICATION" \
        "次故障分类"
}

assert_network_classification() {
    local expected="$1"
    local console_text="$2"
    local packer_text="$3"
    local actual

    printf '%s\n' "$console_text" > "${CLASSIFIER_CONTEXT}/console.log"
    printf '%s\n' "$packer_text" > "${CLASSIFIER_CONTEXT}/packer.log"
    actual="$(guest_network_failure_classification \
        "${CLASSIFIER_CONTEXT}/console.log" \
        "${CLASSIFIER_CONTEXT}/packer.log")"
    assert_equals "$expected" "$actual" "网络故障分类"
}

INSTALL_COMPLETE=$'COSIM_AUTOINSTALL_START\nCOSIM_AUTOINSTALL_COMPLETE'
assert_failure_classification external_interrupt guest_network_tls_failure \
    0 0 141 141 130 "$INSTALL_COMPLETE" \
    $'repo.radeon.com\nConnection timed out [IP: 192.0.2.1 443]' \
    $'Err:1 https://repo.radeon.com/amdgpu\nCertificate verification failed: certificate is not yet valid\nCancelling build after receiving interrupt'
assert_failure_classification host_timeout none 0 0 124 124 0 "$INSTALL_COMPLETE" \
    '' 'Cancelling build after receiving interrupt'
assert_failure_classification artifact_write_failure none 0 0 1 1 1 \
    "$INSTALL_COMPLETE" '' ''
assert_failure_classification guest_network_tls_failure none 0 0 1 1 0 \
    "$INSTALL_COMPLETE" \
    $'Err:1 https://repo.radeon.com/amdgpu\nCertificate verification failed: certificate is not yet valid' ''
assert_failure_classification guest_network_timeout none 0 0 1 1 0 \
    "$INSTALL_COMPLETE" \
    $'Err:1 https://repo.radeon.com/amdgpu\nConnection timed out' ''

assert_network_classification none 'repo.radeon.com' 'Connection timed out'
assert_network_classification none 'repo.radeon.com' \
    'Certificate verification failed: certificate is not yet valid'
assert_network_classification none \
    $'repo.radeon.com\nunrelated output\nConnection timed out' ''
assert_network_classification none \
    $'Err:1 https://repo.radeon.com/amdgpu\nunrelated output\nCertificate verification failed: certificate is not yet valid' ''
assert_network_classification none \
    $'Hit:1 https://repo.radeon.com/amdgpu\nConnection timed out' ''
assert_network_classification none \
    $'COSIM_APT_ATTEMPT_BEGIN command=update attempt=1\nErr:1 https://repo.radeon.com/amdgpu\nConnection timed out\nCOSIM_APT_ATTEMPT_FAILURE command=update attempt=1\nCOSIM_APT_ATTEMPT_BEGIN command=update attempt=2\nHit:1 https://repo.radeon.com/amdgpu\nCOSIM_APT_ATTEMPT_SUCCESS command=update attempt=2' \
    ''
assert_network_classification none \
    $'COSIM_APT_ATTEMPT_BEGIN command=update attempt=1\nErr:1 https://repo.radeon.com/amdgpu\nCertificate verification failed: certificate is not yet valid\nCOSIM_APT_ATTEMPT_FAILURE command=update attempt=1\nCOSIM_APT_ATTEMPT_BEGIN command=update attempt=2\nHit:1 https://repo.radeon.com/amdgpu\nCOSIM_APT_ATTEMPT_SUCCESS command=update attempt=2' \
    ''

# 使用离线 Packer/timeout 替身验证阶段短路和退出状态，不启动虚拟机。
PACKER_TEST_ROOT="${TEST_CONTEXT}/packer-stages"
PACKER_TEST_CONTEXT="${PACKER_TEST_ROOT}/context"
PACKER_STUB_BIN_DIR="${PACKER_TEST_ROOT}/bin"
mkdir -p "$PACKER_TEST_CONTEXT" "$PACKER_STUB_BIN_DIR"
: > "${PACKER_TEST_CONTEXT}/x86-ubuntu-gpu-ml.pkr.hcl"
PACKER_STUB_CALL_LOG="${PACKER_TEST_ROOT}/calls.log"
export PACKER_STUB_CALL_LOG
PACKER_BIN="${PACKER_STUB_BIN_DIR}/packer"
QEMU_BIN="${PACKER_TEST_ROOT}/qemu-system-x86_64"
PACKER_CACHE_DIR="${PACKER_TEST_ROOT}/cache"
PACKER_PLUGIN_ROOT="${PACKER_TEST_ROOT}/plugins"
PACKER_CONFIG_ROOT="${PACKER_TEST_ROOT}/config"
export QEMU_BIN PACKER_CACHE_DIR PACKER_PLUGIN_ROOT PACKER_CONFIG_ROOT

cat > "$PACKER_BIN" <<'EOF'
#!/bin/bash
set -euo pipefail
stage="${1:-missing}"
printf '%s\n' "$stage" >> "$PACKER_STUB_CALL_LOG"
if [[ "$stage" == "build" ]]; then
    printf '%s\n' "Connected to SSH"
fi
case "$stage" in
    init) exit "${PACKER_STUB_INIT_RC:-0}" ;;
    validate) exit "${PACKER_STUB_VALIDATE_RC:-0}" ;;
    build) exit "${PACKER_STUB_BUILD_RC:-0}" ;;
    *) exit 64 ;;
esac
EOF
chmod +x "$PACKER_BIN"

cat > "${PACKER_STUB_BIN_DIR}/timeout" <<'EOF'
#!/bin/bash
set -euo pipefail
while [[ "${1:-}" == --* ]]; do
    shift
done
shift
exec "$@"
EOF
chmod +x "${PACKER_STUB_BIN_DIR}/timeout"
export PATH="${PACKER_STUB_BIN_DIR}:${PATH}"

assert_packer_stage_failure() {
    local label="$1"
    local init_stub_rc="$2"
    local validate_stub_rc="$3"
    local build_stub_rc="$4"
    local expected_calls="$5"
    local expected_init_rc="$6"
    local expected_validate_rc="$7"
    local expected_build_rc="$8"
    local expected_pipeline_rc="$9"
    local expected_classification="${10}"
    local artifact_dir="${PACKER_TEST_ROOT}/${label}"
    local actual_calls

    mkdir -p "$artifact_dir"
    install -m 0600 /dev/null "${artifact_dir}/installer-serial.log"
    install -m 0600 /dev/null "${artifact_dir}/packer.log"
    printf '%s\n' "$INSTALL_COMPLETE" > \
        "${artifact_dir}/installer-serial.log"
    : > "$PACKER_STUB_CALL_LOG"
    export PACKER_STUB_INIT_RC="$init_stub_rc"
    export PACKER_STUB_VALIDATE_RC="$validate_stub_rc"
    export PACKER_STUB_BUILD_RC="$build_stub_rc"

    if run_guest_packer_pipeline "$PACKER_TEST_CONTEXT" "$artifact_dir"; then
        fail "Packer 阶段失败案例意外通过：${label}"
    fi
    actual_calls="$(paste -sd, "$PACKER_STUB_CALL_LOG")"
    assert_equals "$expected_calls" "$actual_calls" \
        "${label} 的 Packer 阶段调用"
    assert_equals "$expected_init_rc" "$GUEST_PACKER_INIT_EXIT_CODE" \
        "${label} 的 Packer init 状态"
    assert_equals "$expected_validate_rc" "$GUEST_PACKER_VALIDATE_EXIT_CODE" \
        "${label} 的 Packer validate 状态"
    assert_equals "$expected_build_rc" "$GUEST_PACKER_BUILD_EXIT_CODE" \
        "${label} 的 Packer build 状态"
    assert_equals "$expected_pipeline_rc" \
        "$GUEST_BUILD_PIPELINE_EXIT_CODE" \
        "${label} 的 Packer 管道状态"
    assert_equals 0 "$GUEST_TEE_EXIT_CODE" \
        "${label} 的 tee 状态"

    classify_guest_build_failure \
        "$GUEST_PACKER_INIT_EXIT_CODE" \
        "$GUEST_PACKER_VALIDATE_EXIT_CODE" \
        "$GUEST_PACKER_BUILD_EXIT_CODE" \
        "$GUEST_BUILD_PIPELINE_EXIT_CODE" \
        "$GUEST_TEE_EXIT_CODE" \
        "${artifact_dir}/installer-serial.log" \
        "${artifact_dir}/console.log" \
        "${artifact_dir}/packer.log"
    assert_equals "$expected_classification" \
        "$GUEST_FAILURE_CLASSIFICATION" \
        "${label} 的 Packer 故障分类"
    write_guest_attempt_status "${artifact_dir}/attempt-status.txt" \
        failed "$GUEST_FAILURE_CLASSIFICATION" \
        "$GUEST_FAILURE_SECONDARY_CLASSIFICATION" "$label"
    assert_equals "$expected_init_rc" \
        "$(metadata_value "${artifact_dir}/attempt-status.txt" \
            packer_init_exit_code)" \
        "${label} 的尝试元数据 init 状态"
    assert_equals "$expected_validate_rc" \
        "$(metadata_value "${artifact_dir}/attempt-status.txt" \
            packer_validate_exit_code)" \
        "${label} 的尝试元数据 validate 状态"
    assert_equals "$expected_build_rc" \
        "$(metadata_value "${artifact_dir}/attempt-status.txt" \
            packer_build_exit_code)" \
        "${label} 的尝试元数据 build 状态"
    assert_equals "$expected_pipeline_rc" \
        "$(metadata_value "${artifact_dir}/attempt-status.txt" \
            build_pipeline_exit_code)" \
        "${label} 的尝试元数据管道状态"
    assert_equals 0 \
        "$(metadata_value "${artifact_dir}/attempt-status.txt" tee_exit_code)" \
        "${label} 的尝试元数据 tee 状态"
    assert_equals "$expected_pipeline_rc" \
        "$(metadata_value "${artifact_dir}/attempt-status.txt" \
            packer_exit_code)" \
        "${label} 的尝试元数据兼容状态"
}

assert_packer_stage_failure init_failure 31 0 0 \
    init 31 not_run not_run 31 packer_init_failure
assert_packer_stage_failure validate_failure 0 32 0 \
    init,validate 0 32 not_run 32 packer_validate_failure
assert_packer_stage_failure build_failure 0 0 33 \
    init,validate,build 0 0 33 33 packer_provisioner_failure

missing_context_artifact="${PACKER_TEST_ROOT}/missing-context-artifact"
mkdir -p "$missing_context_artifact"
install -m 0600 /dev/null \
    "${missing_context_artifact}/installer-serial.log"
install -m 0600 /dev/null "${missing_context_artifact}/packer.log"
: > "$PACKER_STUB_CALL_LOG"
if run_guest_packer_pipeline "${PACKER_TEST_ROOT}/missing-context" \
    "$missing_context_artifact"; then
    fail "Packer 管道错误接受了不存在的构建上下文"
fi
assert_equals '' "$(cat "$PACKER_STUB_CALL_LOG")" \
    "上下文切换失败后的 Packer 调用"
assert_equals not_run "$GUEST_PACKER_INIT_EXIT_CODE" \
    "上下文切换失败后的 Packer init 状态"
assert_equals 125 "$GUEST_BUILD_PIPELINE_EXIT_CODE" \
    "上下文切换失败后的 Packer 管道状态"
classify_guest_build_failure \
    "$GUEST_PACKER_INIT_EXIT_CODE" \
    "$GUEST_PACKER_VALIDATE_EXIT_CODE" \
    "$GUEST_PACKER_BUILD_EXIT_CODE" \
    "$GUEST_BUILD_PIPELINE_EXIT_CODE" \
    "$GUEST_TEE_EXIT_CODE" \
    "${missing_context_artifact}/installer-serial.log" \
    "${missing_context_artifact}/console.log" \
    "${missing_context_artifact}/packer.log"
assert_equals packer_stage_status_failure "$GUEST_FAILURE_CLASSIFICATION" \
    "上下文切换失败分类"

incomplete_stage_status="${PACKER_TEST_ROOT}/incomplete-stage-status.txt"
printf '%s\n' 'packer_validate_exit_code=0' \
    'packer_build_exit_code=0' > "$incomplete_stage_status"
assert_equals not_recorded \
    "$(read_guest_packer_stage_value \
        "$incomplete_stage_status" packer_init_exit_code)" \
    "缺失的 Packer 阶段状态值"

empty_stage_status="${PACKER_TEST_ROOT}/empty-stage-status.txt"
printf '%s\n' 'packer_init_exit_code=' > "$empty_stage_status"
assert_equals not_recorded \
    "$(read_guest_packer_stage_value \
        "$empty_stage_status" packer_init_exit_code)" \
    "空 Packer 阶段状态值"

for malformed_value in abc 00 256 9999; do
    malformed_stage_status="${PACKER_TEST_ROOT}/malformed-${malformed_value}.txt"
    printf 'packer_init_exit_code=%s\n' "$malformed_value" > \
        "$malformed_stage_status"
    assert_equals not_recorded \
        "$(read_guest_packer_stage_value \
            "$malformed_stage_status" packer_init_exit_code)" \
        "畸形 Packer 阶段状态值 ${malformed_value}"
done
assert_equals not_recorded \
    "$(read_guest_packer_stage_value \
        "${PACKER_TEST_ROOT}/missing-stage-status.txt" \
        packer_init_exit_code)" \
    "缺失的 Packer 阶段状态文件"

corrupt_sidecar_artifact="${PACKER_TEST_ROOT}/corrupt-sidecar"
mkdir -p "$corrupt_sidecar_artifact"
install -m 0600 /dev/null \
    "${corrupt_sidecar_artifact}/installer-serial.log"
install -m 0600 /dev/null "${corrupt_sidecar_artifact}/packer.log"
: > "$PACKER_STUB_CALL_LOG"
export PACKER_STUB_INIT_RC=0
export PACKER_STUB_VALIDATE_RC=0
export PACKER_STUB_BUILD_RC=0
if (
    read_guest_packer_stage_value() {
        # shellcheck disable=SC2317
        printf '%s\n' "not_recorded"
    }
    run_guest_packer_pipeline "$PACKER_TEST_CONTEXT" \
        "$corrupt_sidecar_artifact"
); then
    fail "Packer 管道错误接受了损坏的阶段状态"
fi
assert_equals init,validate,build \
    "$(paste -sd, "$PACKER_STUB_CALL_LOG")" \
    "阶段状态损坏时的 Packer 调用"

git -C "$RESOURCES_DIR" archive HEAD src/x86-ubuntu-gpu-ml |
    tar -x -C "$TEST_CONTEXT"
patch --directory="$TEST_CONTEXT" --strip=1 --fuzz=0 --batch \
    --input="$GUEST_PATCH" >/dev/null

ROCM_SCRIPT="${TEST_CONTEXT}/src/x86-ubuntu-gpu-ml/scripts/rocm-install.sh"
PACKER_TEMPLATE="${TEST_CONTEXT}/src/x86-ubuntu-gpu-ml/x86-ubuntu-gpu-ml.pkr.hcl"
bash -n "$ROCM_SCRIPT"
"$SHELLCHECK_BIN" "$ROCM_SCRIPT"
assert_contains 'set -euo pipefail' "$ROCM_SCRIPT"
assert_contains "amdgpu-dkms=\${AMDGPU_DKMS_VERSION}" "$ROCM_SCRIPT"
assert_contains "rocm=\${ROCM_VERSION}" "$ROCM_SCRIPT"
assert_contains 'KERNEL_DEB_DIR="/home/gem5/kernel-debs"' "$ROCM_SCRIPT"
assert_contains 'readonly APT_COMMAND_ATTEMPTS=3' "$ROCM_SCRIPT"
assert_contains 'readonly APT_ACQUIRE_RETRIES=5' "$ROCM_SCRIPT"
assert_contains 'readonly NETWORK_TIMEOUT_SECONDS=30' "$ROCM_SCRIPT"
assert_contains 'apt_get_with_retry update' "$ROCM_SCRIPT"
assert_contains 'APT::Update::Error-Mode=any' "$ROCM_SCRIPT"
assert_contains 'COSIM_APT_ATTEMPT_BEGIN' "$ROCM_SCRIPT"
assert_contains 'COSIM_APT_ATTEMPT_SUCCESS' "$ROCM_SCRIPT"
assert_contains 'COSIM_APT_ATTEMPT_FAILURE' "$ROCM_SCRIPT"
# 合同检查需要匹配字面量 Shell 插值。
# shellcheck disable=SC2016
assert_contains 'Acquire::https::Timeout=${NETWORK_TIMEOUT_SECONDS}' "$ROCM_SCRIPT"
# shellcheck disable=SC2016
assert_contains 'wget --https-only --timeout="$NETWORK_TIMEOUT_SECONDS"' "$ROCM_SCRIPT"
# 合同检查需要匹配字面量 Shell 插值。
# shellcheck disable=SC2016
assert_contains 'apt_get_with_retry install -y "${kernel_debs[@]}"' "$ROCM_SCRIPT"

# 只提取实际补丁中的 APT 重试函数，并用本地函数替身验证严格更新重试。
# shellcheck disable=SC1090
source <(sed -n '/^readonly APT_COMMAND_ATTEMPTS=/,/^}/p' "$ROCM_SCRIPT")
APT_STUB_CALL_LOG="${TEST_CONTEXT}/apt-calls.log"
APT_STUB_SLEEP_LOG="${TEST_CONTEXT}/apt-sleeps.log"
APT_STUB_CALL_COUNT=0
: > "$APT_STUB_CALL_LOG"
: > "$APT_STUB_SLEEP_LOG"

apt-get() {
    APT_STUB_CALL_COUNT=$((APT_STUB_CALL_COUNT + 1))
    printf '%s\n' "$*" >> "$APT_STUB_CALL_LOG"
    (( APT_STUB_CALL_COUNT >= 3 ))
}

sleep() {
    printf '%s\n' "$1" >> "$APT_STUB_SLEEP_LOG"
}

apt_get_with_retry update
assert_equals 3 "$APT_STUB_CALL_COUNT" "严格 APT 更新尝试次数"
assert_equals 2 "$(wc -l < "$APT_STUB_SLEEP_LOG")" \
    "严格 APT 更新重试延迟次数"
assert_equals $'10\n20' "$(cat "$APT_STUB_SLEEP_LOG")" \
    "严格 APT 更新重试延迟"
if grep -Fv -- 'APT::Update::Error-Mode=any' "$APT_STUB_CALL_LOG" |
    grep -q .; then
    fail "APT 更新重试缺少严格错误选项"
fi
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
if grep -Eq -- '--no-check-certificate|Verify-Peer.*false|trusted=yes|AllowInsecureRepositories' \
    "$ROCM_SCRIPT"; then
    fail "Guest 网络重试合同禁用了 TLS 或软件源验证"
fi
# Contract checks require literal shell interpolation.
# shellcheck disable=SC2016
if grep -F 'apt -y install "linux-image-${KERNEL}"' "$ROCM_SCRIPT" >/dev/null; then
    fail "guest kernel installation still depends on the rolling Ubuntu index"
fi

echo "[PASS] guest build contract"
