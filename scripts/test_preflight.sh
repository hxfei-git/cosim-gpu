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

echo "[PASS] preflight contract"
