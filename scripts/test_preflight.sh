#!/bin/bash
# Focused contract tests for scripts/cosim_preflight.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PREFLIGHT="${SCRIPT_DIR}/cosim_preflight.sh"
GUEST_LOCK="${REPO_ROOT}/configs/cosim/guest.lock"
mkdir -p "${REPO_ROOT}/artifacts"
TEST_ROOT="$(mktemp -d "${REPO_ROOT}/artifacts/preflight-test.XXXXXX")"

cleanup() {
    rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

"$PREFLIGHT" --help >/dev/null

# Contract check requires the literal shell variable reference.
# shellcheck disable=SC2016
grep -F 'check_regular_file run.guest_setup "$guest_setup" 1 false' \
    "$PREFLIGHT" >/dev/null || \
    fail "run preflight requires the non-executable Packer source template to be executable"
grep -F "add_check run.guest_provenance PASS \"\$required\"" "$PREFLIGHT" \
    >/dev/null || fail "run preflight does not expose Guest provenance"
grep -F 'guest-provenance.json' "$PREFLIGHT" >/dev/null || \
    fail "run preflight does not write structured Guest provenance"
grep -F 'kernel=full-sha256; m5=full-sha256' "$PREFLIGHT" >/dev/null || \
    fail "run preflight does not record full kernel/m5 validation"
# shellcheck disable=SC2016
grep -F 'metadata_has_exact_assignment_keys "$toolchain_lock"' "$PREFLIGHT" \
    >/dev/null || fail "run preflight 未精确校验 toolchain.lock schema"
# shellcheck disable=SC2016
grep -F 'metadata_has_exact_keys "$qemu_meta"' "$PREFLIGHT" >/dev/null || \
    fail "run preflight 未精确校验 QEMU metadata schema"
grep -F 'meta_signing_verified' "$PREFLIGHT" >/dev/null || \
    fail "run preflight 未校验 QEMU 签名状态"
grep -F 'expected_configure_fingerprint' "$PREFLIGHT" >/dev/null || \
    fail "run preflight 未重算 QEMU configure fingerprint"
grep -F 'expected_build_fingerprint' "$PREFLIGHT" >/dev/null || \
    fail "run preflight 未重算 QEMU build fingerprint"
grep -F 'lock_source_fingerprint' "$PREFLIGHT" >/dev/null || \
    fail "run preflight 未将 QEMU source fingerprint 锚定到 toolchain.lock"
# shellcheck disable=SC2016
grep -F 'is_rfc3339nano "$meta_timestamp"' "$PREFLIGHT" >/dev/null || \
    fail "run preflight 未校验 QEMU metadata timestamp"

if "$PREFLIGHT" host --output-dir /tmp/cosim-preflight-invalid >/dev/null 2>&1; then
    fail "an output directory outside repository artifacts was accepted"
else
    rc=$?
    [[ "$rc" -eq 2 ]] || fail "unsafe output directory returned ${rc}, expected 2"
fi

# 本地桩记录每个 URL，并允许独立控制 HTTP 状态与进程退出码。
fake_bin="${TEST_ROOT}/fake-bin"
mkdir -p "$fake_bin"
cat > "${fake_bin}/curl" <<'SH'
#!/bin/bash
set -euo pipefail

(( $# > 0 )) || exit 64
printf '%s\n' "${!#}" >> "${FAKE_CURL_LOG:?}"
printf '%s' "${FAKE_CURL_HTTP_CODE:?}"
exit "${FAKE_CURL_EXIT_CODE:?}"
SH
chmod +x "${fake_bin}/curl"

locked_url_log="${TEST_ROOT}/locked-urls.log"
locked_url_json="${TEST_ROOT}/locked-urls.json"
locked_url_stderr="${TEST_ROOT}/locked-urls.stderr"
if PATH="${fake_bin}:${PATH}" \
   FAKE_CURL_LOG="$locked_url_log" \
   FAKE_CURL_HTTP_CODE=200 \
   FAKE_CURL_EXIT_CODE=0 \
   COSIM_KVM_DEVICE="${TEST_ROOT}/missing-kvm-locked-urls" \
   "$PREFLIGHT" host --json >"$locked_url_json" 2>"$locked_url_stderr"; then
    fail "缺失 KVM 的离线 URL 合同检查意外成功"
else
    rc=$?
    [[ "$rc" -eq 1 ]] || fail "离线 URL 合同检查返回 ${rc}，预期为 1"
fi

python3 - "$GUEST_LOCK" "$locked_url_log" "$locked_url_json" <<'PY'
import json
import pathlib
import sys

lock_path = pathlib.Path(sys.argv[1])
curl_log_path = pathlib.Path(sys.argv[2])
report_path = pathlib.Path(sys.argv[3])

lock_values = {}
for line in lock_path.read_text().splitlines():
    if not line or line.startswith("#"):
        continue
    key, value = line.split("=", 1)
    lock_values[key] = value

expected = [
    ("network.github", "https://github.com"),
    ("network.qemu", "https://download.qemu.org"),
    ("network.packer", lock_values["PACKER_URL"]),
    ("network.packer_qemu_plugin", lock_values["PACKER_QEMU_PLUGIN_URL"]),
    ("network.ubuntu_iso", lock_values["UBUNTU_ISO_URL"]),
    (
        "network.guest_kernel_image_deb",
        lock_values["GUEST_KERNEL_IMAGE_DEB_URL"],
    ),
    (
        "network.guest_kernel_modules_deb",
        lock_values["GUEST_KERNEL_MODULES_DEB_URL"],
    ),
    (
        "network.guest_kernel_modules_extra_deb",
        lock_values["GUEST_KERNEL_MODULES_EXTRA_DEB_URL"],
    ),
    (
        "network.guest_kernel_headers_deb",
        lock_values["GUEST_KERNEL_HEADERS_DEB_URL"],
    ),
    (
        "network.guest_kernel_headers_generic_deb",
        lock_values["GUEST_KERNEL_HEADERS_GENERIC_DEB_URL"],
    ),
    (
        "network.amdgpu",
        "https://repo.radeon.com/amdgpu/7.0/ubuntu/dists/noble/InRelease",
    ),
    (
        "network.rocm",
        "https://repo.radeon.com/rocm/apt/7.0/dists/noble/InRelease",
    ),
    ("network.ghcr", "https://ghcr.io/v2/"),
]

curl_urls = curl_log_path.read_text().splitlines()
assert curl_urls == [url for _, url in expected], (
    "假 curl 收到的 URL 与锁文件及固定端点不一致",
    curl_urls,
)

report = json.loads(report_path.read_text())
network_checks = [
    check for check in report["checks"] if check["id"].startswith("network.")
]
network_ids = [check["id"] for check in network_checks]
assert network_ids == [check_id for check_id, _ in expected], (
    "网络检查 ID 不稳定或顺序与 URL 调用不一致",
    network_ids,
)
assert len(network_ids) == len(set(network_ids)), "网络检查 ID 必须唯一"
assert all(check["status"] == "PASS" for check in network_checks), network_checks
PY

transport_log="${TEST_ROOT}/transport-failure.log"
transport_json="${TEST_ROOT}/transport-failure.json"
transport_stderr="${TEST_ROOT}/transport-failure.stderr"
if PATH="${fake_bin}:${PATH}" \
   FAKE_CURL_LOG="$transport_log" \
   FAKE_CURL_HTTP_CODE=302 \
   FAKE_CURL_EXIT_CODE=7 \
   COSIM_KVM_DEVICE="${TEST_ROOT}/missing-kvm-transport" \
   "$PREFLIGHT" host --json >"$transport_json" 2>"$transport_stderr"; then
    fail "curl 非零退出码与可接受状态码组合被错误接受"
else
    rc=$?
    [[ "$rc" -eq 1 ]] || fail "传输失败合同检查返回 ${rc}，预期为 1"
fi

python3 - "$transport_json" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text())
checks = {check["id"]: check for check in report["checks"]}
github = checks["network.github"]
assert github["status"] == "FAIL", github
assert "curl_exit=7" in github["detail"], github
assert "http_status=302" in github["detail"], github
PY

terminal_redirect_log="${TEST_ROOT}/terminal-redirect.log"
terminal_redirect_json="${TEST_ROOT}/terminal-redirect.json"
terminal_redirect_stderr="${TEST_ROOT}/terminal-redirect.stderr"
if PATH="${fake_bin}:${PATH}" \
   FAKE_CURL_LOG="$terminal_redirect_log" \
   FAKE_CURL_HTTP_CODE=302 \
   FAKE_CURL_EXIT_CODE=0 \
   COSIM_KVM_DEVICE="${TEST_ROOT}/missing-kvm-terminal-redirect" \
   "$PREFLIGHT" host --json >"$terminal_redirect_json" \
       2>"$terminal_redirect_stderr"; then
    fail "跟随重定向后仍停留在 302 的端点被错误接受"
else
    rc=$?
    [[ "$rc" -eq 1 ]] || fail "终止重定向合同检查返回 ${rc}，预期为 1"
fi

python3 - "$terminal_redirect_json" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text())
checks = {check["id"]: check for check in report["checks"]}
github = checks["network.github"]
assert github["status"] == "FAIL", github
assert "http_status=302" in github["detail"], github
PY

report_dir="${TEST_ROOT}/redaction"
stdout_json="${TEST_ROOT}/stdout.json"
stderr_log="${TEST_ROOT}/stderr.log"
credential_marker="preflight-credential-marker"

if HTTPS_PROXY="http://user:${credential_marker}@proxy.invalid:8080" \
   https_proxy="http://user:${credential_marker}@proxy.invalid:8080" \
   COSIM_KVM_DEVICE="${TEST_ROOT}/missing-kvm" \
   COSIM_PREFLIGHT_SKIP_NETWORK=1 \
   "$PREFLIGHT" host --json --output-dir "$report_dir" \
       >"$stdout_json" 2>"$stderr_log"; then
    fail "required failed or unknown checks returned success"
else
    rc=$?
    [[ "$rc" -eq 1 ]] || fail "failed preflight returned ${rc}, expected 1"
fi

python3 - "$stdout_json" "$report_dir/preflight.json" <<'PY'
import json
import pathlib
import sys

stdout_path = pathlib.Path(sys.argv[1])
artifact_path = pathlib.Path(sys.argv[2])
stdout_report = json.loads(stdout_path.read_text())
artifact_report = json.loads(artifact_path.read_text())
assert stdout_report == artifact_report
assert stdout_report["schema"] == "cosim-preflight-v1"
assert stdout_report["profile"] == "host"
assert stdout_report["overall_status"] == "FAIL"
assert stdout_report["required_failure_count"] > 0
checks = {check["id"]: check for check in stdout_report["checks"]}
assert checks["host.kvm_node"]["status"] == "FAIL"
assert checks["host.kvm_node"]["required"] is True
assert checks["network.github"]["status"] == "UNKNOWN"
assert checks["network.github"]["required"] is True
PY

[[ -s "$report_dir/preflight.txt" ]] || fail "text artifact was not written"
[[ -s "$report_dir/preflight.json" ]] || fail "JSON artifact was not written"

if grep -R -Fq "$credential_marker" "$TEST_ROOT"; then
    fail "credential-bearing proxy URL leaked into preflight output"
fi

# 加载纯函数后，以临时 Git 仓库覆盖 QEMU provenance 的正例与逐字段负例。
# shellcheck disable=SC1090
source "$PREFLIGHT"

qemu_repo="${TEST_ROOT}/qemu-repo"
qemu_prefix="${qemu_repo}/.local/cosim/qemu/${QEMU_VERSION}"
qemu_source="${qemu_repo}/.local/cosim/src/qemu-${QEMU_VERSION}"
qemu_meta="${qemu_repo}/.local/cosim/build/qemu-${QEMU_VERSION}/.cosim-build-meta"
qemu_lock="${qemu_repo}/configs/cosim/toolchain.lock"
qemu_bin="${qemu_prefix}/bin/qemu-system-x86_64"
qemu_img="${qemu_prefix}/bin/qemu-img"
mkdir -p "$(dirname "$qemu_meta")" "$(dirname "$qemu_lock")" \
    "$(dirname "$qemu_bin")" "$qemu_source"
printf '#!/bin/bash\nexit 0\n' > "$qemu_bin"
printf '#!/bin/bash\nexit 0\n# qemu-img\n' > "$qemu_img"
chmod +x "$qemu_bin" "$qemu_img"
printf 'QEMU source fixture\n' > "${qemu_source}/README"
qemu_source_fingerprint="$(qemu_directory_fingerprint \
    "$QEMU_BUILD_SCRIPT" "$qemu_source")"

cat > "$qemu_lock" <<EOF
QEMU_VERSION=${QEMU_VERSION}
QEMU_SOURCE_URL=${QEMU_SOURCE_URL}
QEMU_SIGNATURE_URL=${QEMU_SIGNATURE_URL}
QEMU_RELEASE_KEY_FINGERPRINT=${QEMU_RELEASE_KEY_FINGERPRINT}
QEMU_RELEASE_KEY_URL=${QEMU_RELEASE_KEY_URL}
QEMU_SOURCE_SHA256=1f1209b4db82e6c4417eaf6e7e0b073563572a042d9fb7492b084ba65a9c0693
QEMU_SOURCE_FINGERPRINT=${qemu_source_fingerprint}
EOF
git -C "$qemu_repo" init -q
git -C "$qemu_repo" config user.name "Preflight Contract"
git -C "$qemu_repo" config user.email "preflight-contract@example.invalid"
git -C "$qemu_repo" add configs/cosim/toolchain.lock
git -C "$qemu_repo" commit -q -m "QEMU provenance fixture"

qemu_source_sha="$(metadata_value "$qemu_lock" QEMU_SOURCE_SHA256)"
qemu_binary_sha="$(sha256sum -- "$qemu_bin" | awk '{print $1}')"
qemu_img_sha="$(sha256sum -- "$qemu_img" | awk '{print $1}')"
qemu_configure_args=(
    "--prefix=${qemu_prefix}"
    "--target-list=x86_64-softmmu"
    "--disable-download"
    "--disable-docs"
    "--disable-gtk"
    "--disable-sdl"
    "--disable-werror"
    "--enable-kvm"
    "--enable-slirp"
    "--enable-tools"
    "--enable-virtfs"
)
qemu_configure_fingerprint="$(qemu_array_fingerprint \
    "$QEMU_BUILD_SCRIPT" "${qemu_configure_args[@]}")"
qemu_build_fingerprint="$(qemu_array_fingerprint "$QEMU_BUILD_SCRIPT" \
    "$qemu_source_sha" "$qemu_source_fingerprint" \
    "$qemu_configure_fingerprint")"
qemu_configure_display=""
for qemu_arg in "${qemu_configure_args[@]}"; do
    printf -v qemu_quoted_arg '%q' "$qemu_arg"
    qemu_configure_display+="${qemu_configure_display:+ }${qemu_quoted_arg}"
done

cat > "$qemu_meta" <<EOF
version=${QEMU_VERSION}
source_url=${QEMU_SOURCE_URL}
source_sha256=${qemu_source_sha}
signature_url=${QEMU_SIGNATURE_URL}
signing_key=${QEMU_RELEASE_KEY_FINGERPRINT}
signing_verified=true
initial_source_fingerprint=${qemu_source_fingerprint}
source_fingerprint=${qemu_source_fingerprint}
source_pristine=true
configure_fingerprint=${qemu_configure_fingerprint}
build_fingerprint=${qemu_build_fingerprint}
configure_args=${qemu_configure_display}
binary=${qemu_bin}
binary_sha256=${qemu_binary_sha}
qemu_img=${qemu_img}
qemu_img_sha256=${qemu_img_sha}
compiler=contract compiler
timestamp=2026-08-26T01:02:03.123456789Z
EOF
qemu_meta_baseline="${TEST_ROOT}/qemu-build-meta.baseline"
qemu_lock_baseline="${TEST_ROOT}/toolchain.lock.baseline"
qemu_bin_baseline="${TEST_ROOT}/qemu-system-x86_64.baseline"
qemu_img_baseline="${TEST_ROOT}/qemu-img.baseline"
qemu_source_baseline="${TEST_ROOT}/qemu-source.baseline"
cp -- "$qemu_meta" "$qemu_meta_baseline"
cp -- "$qemu_lock" "$qemu_lock_baseline"
cp -- "$qemu_bin" "$qemu_bin_baseline"
cp -- "$qemu_img" "$qemu_img_baseline"
cp -- "${qemu_source}/README" "$qemu_source_baseline"

validate_qemu_fixture() {
    validate_qemu_provenance "$qemu_repo" "$qemu_bin" "$qemu_img" \
        "$qemu_meta" "$qemu_lock" "$qemu_source" "$QEMU_BUILD_SCRIPT"
}

expect_qemu_provenance_failure() {
    local label="$1"
    local expected_detail="$2"
    local detail

    if detail="$(validate_qemu_fixture)"; then
        fail "${label} 被 QEMU provenance 校验错误接受"
    fi
    [[ "$detail" == *"$expected_detail"* ]] || \
        fail "${label} 未返回预期诊断：${detail}"
}

qemu_detail="$(validate_qemu_fixture)" || \
    fail "完整 QEMU provenance fixture 被拒绝：${qemu_detail}"
[[ "$qemu_detail" == *"metadata_sha256="* && \
   "$qemu_detail" == *"toolchain_lock_sha256="* ]] || \
    fail "成功的 QEMU provenance 未输出 launcher 所需的归档 hash"

printf 'unexpected=value\n' >> "$qemu_meta"
expect_qemu_provenance_failure "额外 metadata 字段" "18 字段 schema 不精确"
cp -- "$qemu_meta_baseline" "$qemu_meta"

printf 'version=%s\n' "$QEMU_VERSION" >> "$qemu_meta"
expect_qemu_provenance_failure "重复 metadata 字段" "18 字段 schema 不精确"
cp -- "$qemu_meta_baseline" "$qemu_meta"

sed -i 's/^signing_verified=true$/signing_verified=false/' "$qemu_meta"
expect_qemu_provenance_failure "未验证签名" "signing_verified=true"
cp -- "$qemu_meta_baseline" "$qemu_meta"

printf 'tampered source\n' >> "${qemu_source}/README"
expect_qemu_provenance_failure "源码 fingerprint 篡改" "源码 fingerprint"
cp -- "$qemu_source_baseline" "${qemu_source}/README"

sed -i "s/^configure_fingerprint=.*/configure_fingerprint=$(printf 'a%.0s' {1..64})/" \
    "$qemu_meta"
expect_qemu_provenance_failure "configure fingerprint 篡改" "configure fingerprint"
cp -- "$qemu_meta_baseline" "$qemu_meta"

sed -i "s/^build_fingerprint=.*/build_fingerprint=$(printf 'b%.0s' {1..64})/" \
    "$qemu_meta"
expect_qemu_provenance_failure "build fingerprint 篡改" "build fingerprint"
cp -- "$qemu_meta_baseline" "$qemu_meta"

printf 'coordinated source tamper\n' >> "${qemu_source}/README"
forged_source_fingerprint="$(qemu_directory_fingerprint \
    "$QEMU_BUILD_SCRIPT" "$qemu_source")"
forged_build_fingerprint="$(qemu_array_fingerprint "$QEMU_BUILD_SCRIPT" \
    "$qemu_source_sha" "$forged_source_fingerprint" \
    "$qemu_configure_fingerprint")"
sed -i \
    -e "s/^initial_source_fingerprint=.*/initial_source_fingerprint=${forged_source_fingerprint}/" \
    -e "s/^source_fingerprint=.*/source_fingerprint=${forged_source_fingerprint}/" \
    -e "s/^build_fingerprint=.*/build_fingerprint=${forged_build_fingerprint}/" \
    "$qemu_meta"
expect_qemu_provenance_failure "源码、metadata 与 build fingerprint 协同篡改" \
    "lock、初始 metadata、当前 metadata 与 live 源码 fingerprint 不一致"
cp -- "$qemu_source_baseline" "${qemu_source}/README"
cp -- "$qemu_meta_baseline" "$qemu_meta"

printf '#!/bin/bash\nexit 7\n' > "$qemu_bin"
chmod +x "$qemu_bin"
expect_qemu_provenance_failure "qemu-system-x86_64 篡改" \
    "qemu-system-x86_64 路径或 SHA-256"
cp -- "$qemu_bin_baseline" "$qemu_bin"
chmod +x "$qemu_bin"

printf '#!/bin/bash\nexit 9\n# qemu-img\n' > "$qemu_img"
chmod +x "$qemu_img"
expect_qemu_provenance_failure "qemu-img 篡改" "qemu-img 路径或 SHA-256"
cp -- "$qemu_img_baseline" "$qemu_img"
chmod +x "$qemu_img"

sed -i 's/^timestamp=.*/timestamp=2026-02-30T01:02:03Z/' "$qemu_meta"
expect_qemu_provenance_failure "非法 metadata timestamp" "RFC3339Nano"
cp -- "$qemu_meta_baseline" "$qemu_meta"

printf 'QEMU_UNEXPECTED=value\n' >> "$qemu_lock"
expect_qemu_provenance_failure "toolchain.lock schema/HEAD 篡改" \
    "toolchain.lock 缺失、符号链接或 assignment schema 不精确"
cp -- "$qemu_lock_baseline" "$qemu_lock"

sed -i "s/^QEMU_SOURCE_SHA256=.*/QEMU_SOURCE_SHA256=$(printf 'd%.0s' {1..64})/" \
    "$qemu_lock"
expect_qemu_provenance_failure "toolchain.lock HEAD 内容篡改" \
    "当前内容不等于 HEAD blob"
cp -- "$qemu_lock_baseline" "$qemu_lock"

sed -i "s/^source_sha256=.*/source_sha256=$(printf 'c%.0s' {1..64})/" \
    "$qemu_meta"
expect_qemu_provenance_failure "metadata 与 lock 的源码 SHA 不一致" \
    "源码及签名身份不一致"
cp -- "$qemu_meta_baseline" "$qemu_meta"

sed -i \
    "s/^QEMU_SOURCE_FINGERPRINT=.*/QEMU_SOURCE_FINGERPRINT=$(printf 'e%.0s' {1..64})/" \
    "$qemu_lock"
git -C "$qemu_repo" add configs/cosim/toolchain.lock
git -C "$qemu_repo" commit -q -m "tamper pinned QEMU source fingerprint"
expect_qemu_provenance_failure "HEAD 中错误的 QEMU source fingerprint pin" \
    "lock、初始 metadata、当前 metadata 与 live 源码 fingerprint 不一致"

echo "[PASS] preflight contract"
