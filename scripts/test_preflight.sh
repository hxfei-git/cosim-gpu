#!/bin/bash
# Focused contract tests for scripts/cosim_preflight.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PREFLIGHT="${SCRIPT_DIR}/cosim_preflight.sh"
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
