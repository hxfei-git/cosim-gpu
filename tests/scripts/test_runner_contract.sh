#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOST_RUNNER="${COSIM_DIR}/scripts/run_cosim_tests.sh"
LAUNCHER="${COSIM_DIR}/scripts/cosim_launch.sh"
PREFLIGHT="${COSIM_DIR}/scripts/cosim_preflight.sh"
GEM5_CONFIG="${COSIM_DIR}/gem5/configs/example/gpufs/mi300_cosim.py"
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
GUEST_EMPTY_LOG="${FIXTURE_DIR}/guest-explicit-empty.log"
if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" '' >"$GUEST_EMPTY_LOG" 2>&1; then
    fail "Guest test runner 接受了显式空位置参数"
fi
grep -Fq 'Invalid test name:' "$GUEST_EMPTY_LOG" || \
    fail "Guest test runner 未将显式空位置参数判定为非法 ID"
if grep -Fq '[RUN] good' "$GUEST_EMPTY_LOG"; then
    fail "Guest test runner 将显式空位置参数退化为了 all-mode"
fi
GUEST_SECOND_POSITION_LOG="${FIXTURE_DIR}/guest-second-position.log"
if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" good good \
    >"$GUEST_SECOND_POSITION_LOG" 2>&1; then
    fail "Guest test runner 接受了第二个位置参数"
fi
grep -Fxq 'Only one exact test name may be supplied.' \
    "$GUEST_SECOND_POSITION_LOG" || \
    fail "Guest test runner 第二位置参数拒绝缺少精确诊断"
if grep -Fq '[RUN] good' "$GUEST_SECOND_POSITION_LOG"; then
    fail "Guest test runner 在拒绝第二位置参数前执行了 fixture"
fi
PROGRAM_ID_128="$(printf 'a%.0s' {1..128})"
PROGRAM_ID_129="${PROGRAM_ID_128}a"
make_fixture "$PROGRAM_ID_128" \
    "echo '[PASS] ${PROGRAM_ID_128}'; exit 0"
TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" "$PROGRAM_ID_128" >/dev/null || \
    fail "Guest test runner 拒绝了 128 字符 program ID"
make_fixture "$PROGRAM_ID_129" \
    "echo '[PASS] ${PROGRAM_ID_129}'; exit 0"
if TEST_BUILD_DIR="$FIXTURE_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" "$PROGRAM_ID_129" >/dev/null 2>&1; then
    fail "Guest test runner 接受了 129 字符 program ID"
fi

GUEST_ALL_DIR="${FIXTURE_DIR}/guest-all"
mkdir "$GUEST_ALL_DIR"
cp -- "${FIXTURE_DIR}/good" "${GUEST_ALL_DIR}/good"
GUEST_ALL_LOG="${FIXTURE_DIR}/guest-no-argument.log"
TEST_BUILD_DIR="$GUEST_ALL_DIR" TEST_TIMEOUT_SECS=2 \
    "${COSIM_DIR}/tests/run_tests.sh" >"$GUEST_ALL_LOG" 2>&1 || \
    fail "Guest test runner 的真正无参数 all-mode 发生回归"
grep -Fq '[RUN] good' "$GUEST_ALL_LOG" || \
    fail "Guest test runner 的真正无参数 all-mode 未执行 fixture"

# Host runner 的证据文件必须在 launcher 启动前固化。
runner_help="$($HOST_RUNNER --help)"
grep -Fq 'COSIM_STRICT_ACCEPTANCE' <<<"$runner_help" || \
    fail "runner help 未说明 COSIM_STRICT_ACCEPTANCE"
grep -Fq '0：允许可重放的 dirty-tree 开发/诊断（默认）' <<<"$runner_help" || \
    fail "runner help 未说明默认 diagnostic 语义"
grep -Fq '1：strict v2 候选；要求顶层仓库与 gem5/ clean' <<<"$runner_help" || \
    fail "runner help 未说明 strict clean-tree 门禁"
grep -Fq '且 tracked baseline lock 与 HEAD 一致' <<<"$runner_help" || \
    fail "runner help 未说明 strict tracked-lock 门禁"

HOST_EMPTY_LOG="${FIXTURE_DIR}/host-explicit-empty.log"
if COSIM_RUN_ID=contract-explicit-empty \
    "$HOST_RUNNER" '' >"$HOST_EMPTY_LOG" 2>&1; then
    fail "Host runner 接受了显式空位置参数"
fi
grep -Fq 'invalid operator name:' "$HOST_EMPTY_LOG" || \
    fail "Host runner 未将显式空位置参数判定为非法 ID"
if grep -Fq 'Host-side single-operator cosim test runner.' "$HOST_EMPTY_LOG"; then
    fail "Host runner 将显式空位置参数退化为了 usage 成功路径"
fi
HOST_NO_ARGUMENT_LOG="${FIXTURE_DIR}/host-no-argument.log"
COSIM_RUN_ID=contract-no-argument \
    "$HOST_RUNNER" >"$HOST_NO_ARGUMENT_LOG" 2>&1 || \
    fail "Host runner 的真正无参数 usage 路径发生回归"
grep -Fq 'Host-side single-operator cosim test runner.' \
    "$HOST_NO_ARGUMENT_LOG" || \
    fail "Host runner 的真正无参数调用未显示 usage"

# shellcheck disable=SC2016
launch_line="$(grep -nF 'setsid stdbuf -oL -eL "$LAUNCH_SCRIPT"' "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
for needle in \
    '} > "${PATCH_DIR}/binary-provenance.txt"' \
    '} > "${RUNNER_ARTIFACT_DIR}/runner-invocation.txt"' \
    'cosim_log_evidence.py" render-guest-script'; do
    evidence_line="$(grep -nF "$needle" "$HOST_RUNNER" | cut -d: -f1)"
    [[ -n "$evidence_line" && "$evidence_line" -lt "$launch_line" ]] || \
        fail "pre-launch evidence is not persisted before setsid: $needle"
done

grep -Fq 'ORIGINAL_ARGS=("$@")' "$HOST_RUNNER" || \
    fail "runner does not preserve the original argv"
grep -Fq "cosim_print_shell_words \"\${ORIGINAL_ARGS[@]}\"" "$HOST_RUNNER" || \
    fail "runner argv does not preserve argument boundaries"
grep -Fq "cosim_print_shell_words \"\${PASSTHROUGH_ARGS[@]}\"" "$HOST_RUNNER" || \
    fail "runner passthrough argv 没有使用统一序列化合同"
grep -Fq "cosim_print_shell_words \"\${ORIGINAL_ARGS[@]}\"" "$LAUNCHER" || \
    fail "launcher argv 没有使用统一序列化合同"
grep -Fq -- '--share-dir|--artifact-dir|--evidence-test-id|--evidence-token)' \
    "$HOST_RUNNER" || \
    fail "runner 未拒绝调用方覆盖内部 launcher 路径或证据身份"
# shellcheck disable=SC2016
grep -Fq 'cp -- "$GUEST_SCRIPT_ARCHIVE" "$GUEST_SCRIPT_HOST"' "$HOST_RUNNER" || \
    fail "Guest staging script is not copied from the archived script"
# shellcheck disable=SC2016
post_token_alive_line="$(grep -nF 'detached session exited after emitting the test completion token' \
    "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
guest_finished_line="$(grep -nF 'GUEST_TEST_FINISHED_AT=' "$HOST_RUNNER" | cut -d: -f1)"
[[ -n "$post_token_alive_line" && -n "$guest_finished_line" && \
   "$post_token_alive_line" -lt "$guest_finished_line" ]] || \
    fail "runner does not confirm session liveness after the completion token"
sed -n "$((post_token_alive_line - 3)),${post_token_alive_line}p" "$HOST_RUNNER" | \
    grep -Fq 'if ! session_alive; then' || \
    fail "post-token exit diagnostic is not guarded by session_alive"
# shellcheck disable=SC2016
grep -Fq '} >> "${PATCH_DIR}/binary-provenance.txt"' "$HOST_RUNNER" || \
    fail "test binary provenance is not appended"
for field in boot_timeout test_timeout guest_run_timeout; do
    grep -Fq "echo \"${field}=\${" "$HOST_RUNNER" || \
        fail "runner metadata lacks ${field}"
done
# shellcheck disable=SC2016
grep -Fq 'warn "Console pipe: ${SESSION_FIFO}"' "$HOST_RUNNER" || \
    fail "SIGINT keep-alive guidance lacks the console pipe"
grep -Fq 'launcher_sha256=' "$HOST_RUNNER" || \
    fail "source snapshot does not identify the launcher"
# shellcheck disable=SC2016
grep -Fq 'RECORDED_GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=${GUEST_HSA_ENABLE_INTERRUPT}"' \
    "$HOST_RUNNER" || fail "default Guest prefix is not canonicalized for evidence"
# shellcheck disable=SC2016
grep -Fq 'echo "cwd=$(pwd -P)"' "$HOST_RUNNER" || \
    fail "runner invocation does not preserve cwd"
grep -Fq "printf 'argv0=%q\\n' \"\$0\"" "$HOST_RUNNER" || \
    fail "runner invocation does not preserve argv0"
# shellcheck disable=SC2016
grep -Fq 'error "--gem5-bin must resolve to ${CANONICAL_GEM5_BIN}"' \
    "$HOST_RUNNER" || fail "runner does not require the canonical gem5 binary"
# shellcheck disable=SC2016
grep -Fq 'error "--gem5-bin must resolve to ${CANONICAL_GEM5_BIN}"' \
    "$LAUNCHER" || fail "launcher does not require the canonical gem5 binary"
grep -Fq 'C_GEM5_BIN="/gem5/build/VEGA_X86/gem5.opt"' "$LAUNCHER" || \
    fail "launcher container binary is not canonical"
# shellcheck disable=SC2016
grep -Fq 'cosim_recorded_runner_category "$ARTIFACT_DIR"' "$LAUNCHER" || \
    fail "launcher signal cleanup does not preserve the validated runner category"
grep -Fq "trap 'handle_signal' INT TERM" "$LAUNCHER" || \
    fail "launcher signals bypass the runner-category handshake"
# shellcheck disable=SC2016
grep -Fq 'error "--screen-log must equal ${CANONICAL_SCREEN_LOG}"' \
    "$HOST_RUNNER" || fail "runner does not fix the console log to artifact/qemu.log"
# shellcheck disable=SC2016
[[ "$(grep -Fc 'echo "  Run-ID:     $COSIM_RUN_ID"' "$LAUNCHER")" -eq 1 ]] || \
    fail "launcher 未输出状态机要求的唯一 canonical Run-ID marker"

for key in repo_status_sha256 gem5_status_sha256 gem5_patch_sha256 \
           gem5_untracked_list_sha256 gem5_untracked_archive_sha256 \
           gem5_build_meta_sha256 gem5_baseline_lock_sha256; do
    grep -Fq "${key}=" "$HOST_RUNNER" || \
        fail "source snapshot lacks ${key}"
done
# shellcheck disable=SC2016
grep -Fq 'load_gem5_build_metadata "$GEM5_BUILD_META_ARCHIVE"' "$HOST_RUNNER" || \
    fail "runner does not validate canonical gem5 build metadata"
# shellcheck disable=SC2016
grep -Fq 'GEM5_CURRENT_SOURCE_FINGERPRINT="$(current_gem5_source_fingerprint)"' \
    "$HOST_RUNNER" || fail "runner does not recompute the gem5 source fingerprint"
# shellcheck disable=SC2016
grep -Fq 'cp -- "$GEM5_BUILD_META" "$GEM5_BUILD_META_ARCHIVE"' \
    "$HOST_RUNNER" || fail "runner does not archive gem5 build metadata"
# shellcheck disable=SC2016
grep -Fq 'cp -- "$GEM5_BASELINE_LOCK" "$GEM5_BASELINE_LOCK_ARCHIVE"' \
    "$HOST_RUNNER" || fail "runner does not archive the tracked gem5 baseline lock"
# shellcheck disable=SC2016
grep -Fq 'git -C "$COSIM_DIR" diff --quiet HEAD -- "$GEM5_BASELINE_LOCK_REL"' \
    "$HOST_RUNNER" || fail "runner lacks the strict tracked-lock gate"
# shellcheck disable=SC2016
grep -Fq '[[ ! -s "${PATCH_DIR}/repo-status.txt" ]]' "$HOST_RUNNER" || \
    fail "runner lacks the strict clean top-level gate"
# shellcheck disable=SC2016
grep -Fq '[[ ! -s "${PATCH_DIR}/gem5-status.txt" ]]' "$HOST_RUNNER" || \
    fail "runner lacks the strict clean gem5 gate"
# shellcheck disable=SC2016
grep -Fq 'export COSIM_STRICT_ACCEPTANCE="$STRICT_ACCEPTANCE"' "$HOST_RUNNER" || \
    fail "runner does not export strict acceptance to launcher/preflight"
for required_debug_flag in HSAPacketProcessor GPUCommandProc GPUDisp GPUKernelInfo; do
    grep -Fq "$required_debug_flag" "$HOST_RUNNER" || \
        fail "strict runner 缺少 debug flag 门禁：${required_debug_flag}"
done
grep -Fq "date -u +'%Y-%m-%dT%H:%M:%S.%9NZ'" "$HOST_RUNNER" || \
    fail "runner 未记录 UTC 纳秒 Guest 测试时间窗"
# shellcheck disable=SC2016
grep -Fq 'gem5_log_sha256=${GEM5_LOG_SHA256}' "$HOST_RUNNER" || \
    fail "runner 未在 metadata 记录 gem5.log SHA256"
# shellcheck disable=SC2016
grep -Fq 'evidence-boundaries "$GEM5_EVIDENCE"' "$HOST_RUNNER" || \
    fail "runner 未从结构化 gem5 证据读取流内 BEGIN/END 边界"
# shellcheck disable=SC2016
grep -Fq 'GEM5_EVIDENCE_START_SEQ=""' "$HOST_RUNNER" || \
    fail "runner 未允许失败 artifact 的 BEGIN seq 留空"
# shellcheck disable=SC2016
grep -Fq 'GEM5_EVIDENCE_END_SEQ=""' "$HOST_RUNNER" || \
    fail "runner 未允许失败 artifact 的 END seq 留空"
grep -Fq 'boundary_capture_attempts=1' "$HOST_RUNNER" || \
    fail "非零 result 未使用单次最佳努力 boundary 捕获"
grep -Fq 'boundary_capture_attempts=10' "$HOST_RUNNER" || \
    fail "成功 result 未保留有界 boundary closure 重试"
# shellcheck disable=SC2016
missing_boundary_error_line="$(grep -nF \
    'error "[${TEST_NAME}] 无法确认 gem5 证据流内已闭合的边界"' \
    "$HOST_RUNNER" | cut -d: -f1)"
[[ -n "$missing_boundary_error_line" ]] || \
    fail "runner 缺少成功结果的 boundary closure 门禁"
# shellcheck disable=SC2016
sed -n "$((missing_boundary_error_line - 1)),${missing_boundary_error_line}p" \
    "$HOST_RUNNER" | grep -Fq 'elif [[ "$result_rc" -eq 0 ]]; then' || \
    fail "boundary capture 缺失仍会截断非零 result 的归档路径"
# shellcheck disable=SC2016
invalid_boundary_error_line="$(grep -nF \
    'error "[${TEST_NAME}] gem5 证据流内边界无效"' \
    "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
sed -n "$((invalid_boundary_error_line - 1)),${invalid_boundary_error_line}p" \
    "$HOST_RUNNER" | grep -Fq 'if [[ "$result_rc" -eq 0 ]]; then' || \
    fail "boundary 解析失败仍会截断非零 result 的归档路径"
# shellcheck disable=SC2016
grep -Fq 'if [[ "$result_rc" -eq 0 && ! -x "$EVIDENCE_BOUNDARY_BINARY" ]]' \
    "$HOST_RUNNER" || \
    fail "helper 缺失门禁未限制为成功 result"
for key in gem5_evidence_start_seq gem5_evidence_end_seq \
           gem5_evidence_test_id gem5_evidence_token \
           gem5_evidence_sha256; do
    grep -Fq "${key}=" "$HOST_RUNNER" || \
        fail "runner metadata 缺少 ${key}"
done
# shellcheck disable=SC2016
grep -Fq 'stable-sha256 "$GEM5_EVIDENCE"' "$HOST_RUNNER" || \
    fail "runner 未稳定哈希结构化 gem5 证据"
# shellcheck disable=SC2016
grep -Fq -- '-v "${ARTIFACT_DIR}:/cosim-artifacts"' "$LAUNCHER" || \
    fail "launcher 未绑定 run-scoped artifact 证据目录"
# shellcheck disable=SC2016
grep -Fq '"--evidence-path=$C_GEM5_EVIDENCE"' "$LAUNCHER" || \
    fail "launcher 未传递固定容器证据路径"
# shellcheck disable=SC2016
grep -Fq '"--evidence-run-id=$COSIM_RUN_ID"' "$LAUNCHER" || \
    fail "launcher 未将结构化证据绑定 run ID"
# shellcheck disable=SC2016
grep -Fq '"--evidence-test-id=$EVIDENCE_TEST_ID"' "$LAUNCHER" || \
    fail "launcher 未将 AQL boundary 绑定测试标识"
# shellcheck disable=SC2016
grep -Fq '"--evidence-token=$EVIDENCE_TOKEN"' "$LAUNCHER" || \
    fail "launcher 未将 AQL boundary 绑定 128-bit token"
# shellcheck disable=SC2016
grep -Fq 'TOKEN_RUN_SHA256="$(printf '\''%s'\'' "$COSIM_RUN_ID" | sha256sum' \
    "$HOST_RUNNER" || fail "completion token 未绑定 COSIM_RUN_ID 的 SHA256"
# shellcheck disable=SC2016
grep -Fq 'TOKEN="COSIM_TEST_DONE_${TEST_NAME}_${TOKEN_RUN_SHA256}"' \
    "$HOST_RUNNER" || fail "test completion token 未使用 run ID identity"
# shellcheck disable=SC2016
grep -Fq 'COMPILE_TOKEN="COSIM_COMPILE_DONE_${TEST_NAME}_${TOKEN_RUN_SHA256}"' \
    "$HOST_RUNNER" || fail "compile completion token 未使用 run ID identity"
# shellcheck disable=SC2016
grep -Fq 'BOUNDARY_READY_TOKEN="COSIM_BOUNDARY_READY_${TEST_NAME}_${TOKEN_RUN_SHA256}"' \
    "$HOST_RUNNER" || fail "helper READY marker 未绑定 run/program identity"
# shellcheck disable=SC2016
grep -Fq '[[ "${1:-}" =~ ^[a-z0-9_]{1,128}$ ]]' "$HOST_RUNNER" || \
    fail "runner 未统一 1..128 字符 program ID"
# shellcheck disable=SC2016
grep -Fq 'valid_test_id "$FILTER"' "$HOST_RUNNER" || \
    fail "runner single/repeat 入口未调用统一 program ID validator"
# shellcheck disable=SC2016
grep -Fq 'valid_evidence_test_id "$EVIDENCE_TEST_ID"' "$LAUNCHER" || \
    fail "launcher 未统一 1..128 字符 evidence test ID"
grep -Fq 'export LC_ALL=C' "$HOST_RUNNER" || \
    fail "runner program ID validator 未固定 ASCII locale"
grep -Fq 'export LC_ALL=C' "$LAUNCHER" || \
    fail "launcher test ID validator 未固定 ASCII locale"
grep -Fq 're.fullmatch(r"[a-z0-9_]{1,128}", evidence_test_id)' \
    "$GEM5_CONFIG" || fail "gem5 Python config 未统一 1..128 字符 test ID"
# shellcheck disable=SC2016
grep -Fq 'stable-sha256 "$canonical_path"' "$HOST_RUNNER" || \
    fail "Host 未在 ack 前稳定复核 helper"
# shellcheck disable=SC2016
grep -Fq 'sync -f "${RUNNER_ARTIFACT_DIR}/runner-invocation.txt"' \
    "$HOST_RUNNER" || fail "runner invocation helper 锚点未在 ack 前持久化"
# shellcheck disable=SC2016
grep -Fq 'ln -- "$ack_tmp" "$EVIDENCE_BOUNDARY_ACK"' "$HOST_RUNNER" || \
    fail "helper ack 未使用 no-clobber 原子发布"
# shellcheck disable=SC2016
grep -Fq '"$EVIDENCE_BOUNDARY_BINARY_CANONICAL")' "$HOST_RUNNER" || \
    fail "runner 缺少 helper 最终稳定重哈希"
# shellcheck disable=SC2016
grep -Fq 'stable-sha256 "$SCREEN_LOG"' "$HOST_RUNNER" || \
    fail "runner 未使用 O_NOFOLLOW 稳定快照哈希 QEMU 日志"
# shellcheck disable=SC2016
grep -Fq 'stable-sha256 "$GEM5_LOG"' "$HOST_RUNNER" || \
    fail "runner 未使用 O_NOFOLLOW 稳定快照哈希 gem5 日志"
# shellcheck disable=SC2016
grep -Fq 'qemu_log_sha256=${QEMU_LOG_SHA256}' "$HOST_RUNNER" || \
    fail "runner 未在 metadata 记录 qemu.log SHA256"
# shellcheck disable=SC2016
session_closed_line="$(grep -nF 'exec {CONTROL_FD}>&-' "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
qemu_hash_line="$(grep -nF 'stable-sha256 "$SCREEN_LOG"' "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
gem5_hash_line="$(grep -nF 'stable-sha256 "$GEM5_LOG"' "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
evidence_hash_line="$(grep -nF 'stable-sha256 "$GEM5_EVIDENCE"' \
    "$HOST_RUNNER" | cut -d: -f1)"
classifier_line="$(grep -nF 'classify_runs.py' "$HOST_RUNNER" | tail -n 1 | cut -d: -f1)"
# shellcheck disable=SC2016
metadata_line="$(grep -nF '} > "${RUNNER_ARTIFACT_DIR}/runner-metadata.txt"' \
    "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
cleanup_line="$(grep -nF 'step "[${TEST_NAME}] Cleaning up detached session' \
    "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
matrix_line="$(grep -nF '} > "${RUNNER_ARTIFACT_DIR}/matrix.tsv"' \
    "$HOST_RUNNER" | cut -d: -f1)"
[[ -n "$session_closed_line" && -n "$qemu_hash_line" && \
   -n "$gem5_hash_line" && -n "$evidence_hash_line" && \
   -n "$metadata_line" && -n "$cleanup_line" && -n "$classifier_line" && \
   -n "$matrix_line" && \
   "$metadata_line" -lt "$cleanup_line" && \
   "$session_closed_line" -lt "$qemu_hash_line" && \
   "$qemu_hash_line" -lt "$gem5_hash_line" && \
   "$gem5_hash_line" -lt "$evidence_hash_line" && \
   "$evidence_hash_line" -lt "$classifier_line" && \
   "$classifier_line" -lt "$matrix_line" ]] || \
    fail "失败结果未保持 metadata、cleanup、稳定哈希、verdict、matrix 完整归档顺序"
# shellcheck disable=SC2016
[[ "$(grep -Fc 'echo "strict_acceptance=${STRICT_ACCEPTANCE}"' \
    "$HOST_RUNNER")" -eq 2 ]] || \
    fail "runner invocation and metadata do not both record strict acceptance"
grep -Fq 'guest_run_timeout\tstrict_acceptance\n' "$HOST_RUNNER" || \
    fail "local matrix does not record strict acceptance"
# shellcheck disable=SC2016
grep -Fq 'metadata_has_exact_keys "$GEM5_BASELINE_LOCK"' "$PREFLIGHT" || \
    fail "run preflight does not require the exact baseline-lock schema"
grep -Fq 'top-level source tree is not clean' "$PREFLIGHT" || \
    fail "run preflight does not require a clean top-level tree"
grep -Fq 'gem5 source tree is not clean' "$PREFLIGHT" || \
    fail "run preflight lacks the strict clean gem5 gate"
grep -Fq 'run.strict_acceptance' "$PREFLIGHT" || \
    fail "run preflight does not record its acceptance mode"
[[ "$(grep -Fc 'add_check run.gem5_provenance' "$PREFLIGHT")" -eq 2 ]] || \
    fail "run preflight does not emit exactly one gem5 provenance result"

# 结构化 run preflight 与旧资源审计都必须在 gem5 Docker 启动前固化。
# shellcheck disable=SC2016
preflight_line="$(grep -nF '"${SCRIPT_DIR}/cosim_preflight.sh" run --output-dir "$PREFLIGHT_DIR"' \
    "$LAUNCHER" | cut -d: -f1)"
# shellcheck disable=SC2016
overlay_line="$(grep -nF '"$QEMU_IMG" create -q' "$LAUNCHER" | cut -d: -f1)"
docker_line="$(grep -nF 'GEM5_DOCKER_CMD=(' "$LAUNCHER" | cut -d: -f1)"
[[ -n "$preflight_line" && -n "$overlay_line" && -n "$docker_line" && \
   "$preflight_line" -lt "$overlay_line" && "$preflight_line" -lt "$docker_line" ]] || \
    fail "structured run preflight is not gated before runtime resource creation"
# shellcheck disable=SC2016
grep -Fq 'tee "${PREFLIGHT_DIR}/preflight.log"' "$LAUNCHER" || \
    fail "structured run preflight stdout is not archived"
# shellcheck disable=SC2016
grep -Fq '"${ARTIFACT_DIR}/preflight-resources.log"' "$LAUNCHER" || \
    fail "resource preflight log is not archived with the artifact"

if "$HOST_RUNNER" --share-dir /tmp vector_add >/dev/null 2>&1; then
    fail "runner accepted a caller-owned --share-dir"
fi
if "$HOST_RUNNER" --artifact-dir /tmp vector_add >/dev/null 2>&1; then
    fail "runner accepted a caller-owned --artifact-dir"
fi
if "$HOST_RUNNER" --evidence-test-id vector_add vector_add \
        >/dev/null 2>&1; then
    fail "runner 接受了调用方提供的 --evidence-test-id"
fi
if "$HOST_RUNNER" --evidence-token 00000000000000000000000000000000 \
        vector_add >/dev/null 2>&1; then
    fail "runner 接受了调用方提供的 --evidence-token"
fi
if "$HOST_RUNNER" --gem5-bin /tmp/external-gem5.opt vector_add >/dev/null 2>&1; then
    fail "runner accepted a gem5 binary from another source tree"
fi

# 在隔离的小型仓库中执行 producer gate；fake launcher 只用于证明 gate 是否被越过。
PRODUCER_ROOT="${FIXTURE_DIR}/producer-repo"
PRODUCER_RUNNER="${PRODUCER_ROOT}/scripts/run_cosim_tests.sh"
PRODUCER_LAUNCHER="${PRODUCER_ROOT}/scripts/cosim_launch.sh"
STRICT_LAUNCHER="${PRODUCER_ROOT}/scripts/strict-cosim-launch.sh"
PRODUCER_GEM5_BIN="${PRODUCER_ROOT}/gem5/build/VEGA_X86/gem5.opt"
PRODUCER_GEM5_META="${PRODUCER_ROOT}/gem5/build/VEGA_X86/.cosim-build-meta"
PRODUCER_GEM5_LOCK="${PRODUCER_ROOT}/configs/cosim/gem5-baseline.lock"
FIXTURE_GEM5_FINGERPRINT="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
FIXTURE_GEM5_DOCKER_IMAGE="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
export FIXTURE_GEM5_FINGERPRINT FIXTURE_GEM5_DOCKER_IMAGE

mkdir -p "${PRODUCER_ROOT}/scripts" "${PRODUCER_ROOT}/tests/kernels" \
    "${PRODUCER_ROOT}/artifacts" "${PRODUCER_ROOT}/gem5" \
    "${PRODUCER_ROOT}/configs/cosim"
cp "$HOST_RUNNER" "$PRODUCER_RUNNER"
cp "${COSIM_DIR}/scripts/cosim_lib.sh" \
    "${COSIM_DIR}/scripts/cosim_guest_env.sh" \
    "${COSIM_DIR}/scripts/cosim_log_evidence.py" \
    "${COSIM_DIR}/scripts/classify_runs.py" "${PRODUCER_ROOT}/scripts/"
cp "$LAUNCHER" "$STRICT_LAUNCHER"
cat > "${PRODUCER_ROOT}/scripts/cosim_build.sh" <<'EOF'
#!/bin/bash
set -euo pipefail
source_fingerprint() {
    printf '%s\n' "${FIXTURE_GEM5_FINGERPRINT:?}"
}
EOF
cat > "$PRODUCER_LAUNCHER" <<'EOF'
#!/bin/bash
set -euo pipefail
artifact_dir=""
share_dir=""
evidence_test_id=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifact-dir) artifact_dir="$2"; shift 2 ;;
        --share-dir) share_dir="$2"; shift 2 ;;
        --evidence-test-id) evidence_test_id="$2"; shift 2 ;;
        --evidence-token) shift 2 ;;
        *) shift ;;
    esac
done
[[ -n "$artifact_dir" ]] || exit 2
if [[ "${COSIM_RUN_ID:-}" != "archive-compile-failure" && \
      "${COSIM_RUN_ID:-}" != "archive-open-boundary" ]]; then
    printf '%s\n' \
        'result=PASS' \
        'primary_category=infra_unknown' \
        'secondary_category=none' > "${artifact_dir}/cleanup-status.txt"
    echo '[FAKE_LAUNCH_REACHED]'
    exit 1
fi
[[ -n "$share_dir" && -n "$evidence_test_id" ]] || exit 2
cleanup() {
    printf '%s\n' \
        'result=PASS' \
        'primary_category=test_fail' \
        'secondary_category=none' > "${artifact_dir}/cleanup-status.txt"
    exit 0
}
trap cleanup INT TERM
run_sha256="$(printf '%s' "$COSIM_RUN_ID" | sha256sum | awk '{print $1}')"
printf 'gem5 simulation started\n' > "${artifact_dir}/gem5.log"
printf '%s\n' \
    $'schema\trun_id\tseq\ttick\tevent\tgpu\tdispatch\twg\tcu' \
    $'COSIM_GPU_EVIDENCE_V1\t'"${COSIM_RUN_ID}"$'\t0\t0\tsession_start\t-1\t-1\t-1\t-1' \
    > "${artifact_dir}/gem5-evidence.tsv"
echo 'root@gem5:~#'
IFS= read -r _guest_command || true
echo '[COSIM_ENV] HSA_ENABLE_INTERRUPT=0'
echo '[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=1'
if [[ "$COSIM_RUN_ID" == "archive-compile-failure" ]]; then
    echo "__COSIM_COMPILE_DONE_${evidence_test_id}_${run_sha256}__:2"
    echo "__COSIM_TEST_DONE_${evidence_test_id}_${run_sha256}__:2"
else
    mkdir -p "${share_dir}/build" "${share_dir}/tools-build"
    printf 'fixture test binary\n' > "${share_dir}/build/${evidence_test_id}"
    printf 'fixture boundary helper\n' > \
        "${share_dir}/tools-build/cosim_evidence_boundary"
    chmod +x "${share_dir}/build/${evidence_test_id}" \
        "${share_dir}/tools-build/cosim_evidence_boundary"
    boundary_sha256="$(sha256sum \
        "${share_dir}/tools-build/cosim_evidence_boundary" | awk '{print $1}')"
    printf '%s\n' \
        $'COSIM_GPU_EVIDENCE_V1\t'"${COSIM_RUN_ID}"$'\t1\t1\tclient_connected\t0\t-1\t-1\t-1' \
        $'COSIM_GPU_EVIDENCE_V1\t'"${COSIM_RUN_ID}"$'\t2\t2\ttest_begin\t0\t-1\t-1\t-1' \
        >> "${artifact_dir}/gem5-evidence.tsv"
    echo "__COSIM_COMPILE_DONE_${evidence_test_id}_${run_sha256}__:0"
    echo "__COSIM_BOUNDARY_READY_${evidence_test_id}_${run_sha256}__:${boundary_sha256}"
    ack_path="${share_dir}/.cosim_evidence_boundary_ack.${run_sha256}"
    for ((attempt=0; attempt<100; attempt++)); do
        if [[ -f "$ack_path" && ! -L "$ack_path" ]]; then
            rm -f -- "$ack_path"
            break
        fi
        sleep 0.1
    done
    echo "__COSIM_TEST_DONE_${evidence_test_id}_${run_sha256}__:7"
fi
while true; do
    sleep 1
done
EOF
chmod +x "$PRODUCER_RUNNER" "$PRODUCER_LAUNCHER" "$STRICT_LAUNCHER" \
    "${PRODUCER_ROOT}/scripts/cosim_build.sh"
printf 'int vector_add_contract = 1;\n' > \
    "${PRODUCER_ROOT}/tests/kernels/vector_add.cpp"
printf 'artifacts/\ngem5/\n*.log\n' > "${PRODUCER_ROOT}/.gitignore"

git -C "$PRODUCER_ROOT" init -q
git -C "$PRODUCER_ROOT" add .gitignore scripts tests/kernels/vector_add.cpp
git -C "$PRODUCER_ROOT" -c user.name='Contract Test' \
    -c user.email='contract@example.invalid' commit -qm 'fixture: producer contract'

git -C "${PRODUCER_ROOT}/gem5" init -q
printf 'build/\n' > "${PRODUCER_ROOT}/gem5/.gitignore"
printf 'fixture gem5 source\n' > "${PRODUCER_ROOT}/gem5/source.txt"
git -C "${PRODUCER_ROOT}/gem5" add .gitignore source.txt
git -C "${PRODUCER_ROOT}/gem5" -c user.name='Contract Test' \
    -c user.email='contract@example.invalid' commit -qm 'fixture: gem5 source'
mkdir -p "$(dirname "$PRODUCER_GEM5_BIN")"
printf 'fixture gem5 binary\n' > "$PRODUCER_GEM5_BIN"
chmod +x "$PRODUCER_GEM5_BIN"
PRODUCER_GEM5_COMMIT="$(git -C "${PRODUCER_ROOT}/gem5" rev-parse HEAD)"
PRODUCER_GEM5_SHA256="$(sha256sum "$PRODUCER_GEM5_BIN" | awk '{print $1}')"

write_producer_lock() {
    local commit="${1:-$PRODUCER_GEM5_COMMIT}"
    local fingerprint="${2:-$FIXTURE_GEM5_FINGERPRINT}"
    local binary_sha256="${3:-$PRODUCER_GEM5_SHA256}"
    local docker_image="${4:-$FIXTURE_GEM5_DOCKER_IMAGE}"
    printf '%s\n' \
        'schema=1' \
        "gem5_commit=${commit}" \
        'source_fingerprint_algorithm=2' \
        "source_fingerprint=${fingerprint}" \
        "binary_sha256=${binary_sha256}" \
        "docker_image=${docker_image}" > "$PRODUCER_GEM5_LOCK"
}

write_producer_lock
git -C "$PRODUCER_ROOT" add configs/cosim/gem5-baseline.lock
git -C "$PRODUCER_ROOT" -c user.name='Contract Test' \
    -c user.email='contract@example.invalid' commit -qm 'fixture: gem5 baseline lock'

PRODUCER_FAKE_BIN="${FIXTURE_DIR}/producer-fake-bin"
mkdir -p "$PRODUCER_FAKE_BIN"
cat > "${PRODUCER_FAKE_BIN}/docker" <<'EOF'
#!/bin/bash
set -euo pipefail
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
    case "${!#}" in
        gem5-run:local) printf '%s\n' "${FIXTURE_GEM5_DOCKER_IMAGE:?}" ;;
        alternate-run:local)
            printf '%s\n' \
                'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            ;;
        *) exit 1 ;;
    esac
    exit 0
fi
exit 1
EOF
chmod +x "${PRODUCER_FAKE_BIN}/docker"

write_producer_metadata() {
    local commit="${1:-$PRODUCER_GEM5_COMMIT}"
    local fingerprint="${2:-$FIXTURE_GEM5_FINGERPRINT}"
    local binary="${3:-$PRODUCER_GEM5_BIN}"
    local binary_sha256="${4:-$PRODUCER_GEM5_SHA256}"
    local docker_image="${5:-$FIXTURE_GEM5_DOCKER_IMAGE}"
    printf '%s\n' \
        "commit=${commit}" \
        'source_fingerprint_algorithm=2' \
        "source_fingerprint=${fingerprint}" \
        'target=VEGA_X86' \
        "binary=${binary}" \
        "binary_sha256=${binary_sha256}" \
        "docker_image=${docker_image}" > "$PRODUCER_GEM5_META"
}

run_producer_exact_case() {
    local case_name="$1"
    local strict_acceptance="${COSIM_STRICT_ACCEPTANCE:-0}"
    shift
    PATH="${PRODUCER_FAKE_BIN}:${PATH}" \
        COSIM_STRICT_ACCEPTANCE="$strict_acceptance" \
        COSIM_RUN_ID="$case_name" "$PRODUCER_RUNNER" \
        --session-name "contract-${case_name}" \
        --boot-timeout 1 \
        --output-dir "${PRODUCER_ROOT}/artifacts/${case_name}" \
        "$@" > "${PRODUCER_ROOT}/${case_name}.log" 2>&1
}

run_producer_case() {
    local case_name="$1"
    shift
    run_producer_exact_case "$case_name" "$@" vector_add
}

run_archive_failure_case() {
    local case_name="$1"
    local artifact_dir="${PRODUCER_ROOT}/artifacts/${case_name}"

    PATH="${PRODUCER_FAKE_BIN}:${PATH}" \
        PYTHONDONTWRITEBYTECODE=1 COSIM_STRICT_ACCEPTANCE=0 \
        COSIM_RUN_ID="$case_name" \
        "$PRODUCER_RUNNER" \
        --session-name "contract-${case_name}" \
        --boot-timeout 3 \
        --test-timeout 1 \
        --guest-run-timeout 15 \
        --output-dir "$artifact_dir" \
        vector_add > "${PRODUCER_ROOT}/${case_name}.log" 2>&1
}

for control_whitespace in $'\n' $'\r' $'\t'; do
    case "$control_whitespace" in
        $'\n') control_name="newline" ;;
        $'\r') control_name="carriage-return" ;;
        $'\t') control_name="tab" ;;
    esac
    invalid_output="${PRODUCER_ROOT}/artifacts/invalid-${control_name}${control_whitespace}path"
    invalid_log="${PRODUCER_ROOT}/invalid-output-${control_name}.log"
    if PATH="${PRODUCER_FAKE_BIN}:${PATH}" \
        COSIM_RUN_ID="invalid-output-${control_name}" "$PRODUCER_RUNNER" \
        --output-dir "$invalid_output" vector_add > "$invalid_log" 2>&1; then
        fail "runner accepted ${control_name} in --output-dir"
    fi
    grep -Fq 'control whitespace is not allowed in --output-dir' "$invalid_log" || \
        fail "${control_name} output-dir rejection lacked a diagnostic"
    [[ ! -e "$invalid_output" ]] || \
        fail "${control_name} output-dir rejection created an artifact"
done

snapshot_value() {
    local snapshot="$1"
    local key="$2"
    sed -n "s/^${key}=//p" "$snapshot"
}

assert_snapshot_hash() {
    local snapshot="$1"
    local key="$2"
    local file="$3"
    local expected actual
    expected="$(snapshot_value "$snapshot" "$key")"
    actual="$(sha256sum "$file" | awk '{print $1}')"
    [[ "$expected" == "$actual" ]] || \
        fail "producer snapshot hash mismatch: ${key}"
}

write_producer_metadata

if run_producer_exact_case producer-second-position \
        vector_add vector_add; then
    fail "copied Host runner 接受了第二个位置参数"
fi
grep -Fq 'only one operator name may be supplied' \
    "${PRODUCER_ROOT}/producer-second-position.log" || \
    fail "copied Host runner 第二位置参数拒绝缺少精确诊断"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-second-position" ]] || \
    fail "copied Host runner 在拒绝第二位置参数前创建了 artifact"
if grep -Fq '[FAKE_LAUNCH_REACHED]' \
        "${PRODUCER_ROOT}/producer-second-position.log"; then
    fail "copied Host runner 的第二位置参数到达了 fake launcher"
fi

assert_single_mode_child() {
    local case_name="$1"
    local expected_session="$2"
    local case_root="${PRODUCER_ROOT}/artifacts/${case_name}"
    local child_invocation child_artifact expected_argv
    local -a child_invocations=()

    mapfile -d '' -t child_invocations < <(
        find "$case_root" -type f -name runner-invocation.txt -print0
    )
    [[ "${#child_invocations[@]}" -eq 1 ]] || \
        fail "${case_name} 未生成唯一 child runner invocation"
    child_invocation="${child_invocations[0]}"
    child_artifact="${child_invocation%/runner-invocation.txt}"
    printf -v expected_argv \
        'argv= --session-name %q --boot-timeout 1 --test-timeout 60 --guest-run-timeout 1800 --output-dir %q vector_add' \
        "$expected_session" "$child_artifact"
    grep -Fxq "$expected_argv" "$child_invocation" || \
        fail "${case_name} child runner argv 不符合单 program 合同"
    grep -Fxq 'runner_argument=vector_add' "$child_invocation" || \
        fail "${case_name} child runner 未归档 canonical program"
    grep -Fq '[FAKE_LAUNCH_REACHED]' "${child_artifact}/qemu.log" || \
        fail "${case_name} child 未到达隔离 fake launcher"
}

if run_producer_exact_case producer-repeat-one --repeat 1 vector_add; then
    fail "repeat fixture 在 fake launcher 失败后意外成功"
fi
assert_single_mode_child \
    producer-repeat-one contract-producer-repeat-one-repeat-1

if run_producer_exact_case producer-all-one --all; then
    fail "all fixture 在 fake launcher 失败后意外成功"
fi
VECTOR_ADD_SHA256="$(printf '%s' vector_add | sha256sum | awk '{print $1}')"
assert_single_mode_child producer-all-one \
    "contract-producer-all-one-all-${VECTOR_ADD_SHA256:0:16}"

if run_producer_case producer-valid; then
    fail "producer fixture unexpectedly completed a fake launch"
fi
VALID_ARTIFACT="${PRODUCER_ROOT}/artifacts/producer-valid"
VALID_PATCH="${VALID_ARTIFACT}/patch"
grep -Fq '[FAKE_LAUNCH_REACHED]' "${VALID_ARTIFACT}/qemu.log" || \
    fail "valid producer provenance did not reach the launcher boundary"
EXPECTED_GUEST_SCRIPT="${FIXTURE_DIR}/expected-guest-run.sh"
python3 -B "${PRODUCER_ROOT}/scripts/cosim_log_evidence.py" render-guest-script \
    --program vector_add --run-id producer-valid --hsa-enable-interrupt 0 \
    --test-timeout 60 > "$EXPECTED_GUEST_SCRIPT"
cmp -s "$EXPECTED_GUEST_SCRIPT" "${VALID_ARTIFACT}/guest-run.sh" || \
    fail "producer did not archive the shared canonical Guest script"
MIN_TIMEOUT_GUEST_SCRIPT="${FIXTURE_DIR}/minimum-timeout-guest-run.sh"
python3 -B "${PRODUCER_ROOT}/scripts/cosim_log_evidence.py" render-guest-script \
    --program vector_add --run-id producer-timeout-one \
    --hsa-enable-interrupt 0 --test-timeout 1 > "$MIN_TIMEOUT_GUEST_SCRIPT"
boundary_handshake_budget="$(sed -n \
    's/^boundary_handshake_timeout_secs=//p' "$MIN_TIMEOUT_GUEST_SCRIPT")"
[[ "$boundary_handshake_budget" =~ ^[0-9]+$ && \
   "$boundary_handshake_budget" -ge 30 ]] || \
    fail "TEST_TIMEOUT=1 未获得至少 30 秒的独立 boundary handshake 预算"
grep -Fq 'boundary_wait<boundary_handshake_timeout_secs' \
    "$MIN_TIMEOUT_GUEST_SCRIPT" || \
    fail "Guest ack 等待未使用独立 boundary handshake 预算"
# shellcheck disable=SC2016
[[ "$(grep -Fc \
    'timeout --signal=TERM "${boundary_handshake_timeout_secs}s"' \
    "$MIN_TIMEOUT_GUEST_SCRIPT")" -eq 2 ]] || \
    fail "BEGIN/END helper 未统一使用独立 boundary handshake 预算"
grep -Fq 'TEST_TIMEOUT_SECS=1 ./run_tests.sh vector_add' \
    "$MIN_TIMEOUT_GUEST_SCRIPT" || \
    fail "workload 未继续使用调用方 TEST_TIMEOUT=1"
ack_loop_line="$(grep -nF 'boundary_wait<boundary_handshake_timeout_secs' \
    "$MIN_TIMEOUT_GUEST_SCRIPT" | cut -d: -f1)"
# shellcheck disable=SC2016
ack_final_check_line="$(grep -nF 'if [[ -e "$boundary_ack" || -L "$boundary_ack" ]]; then' \
    "$MIN_TIMEOUT_GUEST_SCRIPT" | tail -n 1 | cut -d: -f1)"
ack_timeout_line="$(grep -nF '__:124"' "$MIN_TIMEOUT_GUEST_SCRIPT" | \
    cut -d: -f1)"
[[ -n "$ack_loop_line" && -n "$ack_final_check_line" && \
   -n "$ack_timeout_line" && "$ack_loop_line" -lt "$ack_final_check_line" && \
   "$ack_final_check_line" -lt "$ack_timeout_line" ]] || \
    fail "Guest ack 等待缺少完整预算结束后的末次检查"
# shellcheck disable=SC2016
begin_line="$(grep -nF '"$boundary_tool" begin "$boundary_token"' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
ready_line="$(grep -nF 'echo "__COSIM_BOUNDARY_READY_vector_add_' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
# shellcheck disable=SC2016
ack_line="$(grep -nF 'boundary_ack_sha256="$(sed -n' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
# shellcheck disable=SC2016
pre_begin_hash_line="$(grep -nF 'boundary_pre_begin_sha256="$(sha256sum' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
target_line="$(grep -nF './run_tests.sh vector_add' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
# shellcheck disable=SC2016
pre_end_hash_line="$(grep -nF 'boundary_pre_end_sha256="$(sha256sum' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
# shellcheck disable=SC2016
end_line="$(grep -nF '"$boundary_tool" end "$boundary_token"' \
    "$EXPECTED_GUEST_SCRIPT" | cut -d: -f1)"
token_line="$(grep -nF 'echo "__COSIM_TEST_DONE_vector_add_' \
    "$EXPECTED_GUEST_SCRIPT" | tail -n 1 | cut -d: -f1)"
[[ -n "$ready_line" && -n "$ack_line" && -n "$pre_begin_hash_line" && \
   -n "$begin_line" && -n "$target_line" && -n "$pre_end_hash_line" && \
   -n "$end_line" && \
   -n "$token_line" && "$ready_line" -lt "$ack_line" && \
   "$ack_line" -lt "$pre_begin_hash_line" && \
   "$pre_begin_hash_line" -lt "$begin_line" && \
   "$begin_line" -lt "$target_line" && \
   "$target_line" -lt "$pre_end_hash_line" && \
   "$pre_end_hash_line" -lt "$end_line" && \
   "$end_line" -lt "$token_line" ]] || \
    fail "canonical Guest 脚本未保证 READY、ack、重哈希、BEGIN、目标、END、完成标记顺序"
cmp -s "$PRODUCER_GEM5_META" "${VALID_PATCH}/gem5-build-meta.txt" || \
    fail "archived gem5 metadata differs from the validated source"
cmp -s "$PRODUCER_GEM5_LOCK" "${VALID_PATCH}/gem5-baseline.lock" || \
    fail "archived gem5 baseline lock differs from the tracked source"
VALID_SNAPSHOT="${VALID_PATCH}/source-snapshot.txt"
assert_snapshot_hash "$VALID_SNAPSHOT" gem5_status_sha256 \
    "${VALID_PATCH}/gem5-status.txt"
assert_snapshot_hash "$VALID_SNAPSHOT" gem5_patch_sha256 \
    "${VALID_PATCH}/gem5.patch"
assert_snapshot_hash "$VALID_SNAPSHOT" gem5_untracked_list_sha256 \
    "${VALID_PATCH}/untracked-files.txt"
assert_snapshot_hash "$VALID_SNAPSHOT" gem5_build_meta_sha256 \
    "${VALID_PATCH}/gem5-build-meta.txt"
assert_snapshot_hash "$VALID_SNAPSHOT" gem5_baseline_lock_sha256 \
    "${VALID_PATCH}/gem5-baseline.lock"
assert_snapshot_hash "$VALID_SNAPSHOT" repo_status_sha256 \
    "${VALID_PATCH}/repo-status.txt"
[[ ! -s "${VALID_PATCH}/repo-status.txt" ]] || \
    fail "valid producer fixture did not archive a clean top-level status"
[[ "$(snapshot_value "$VALID_SNAPSHOT" gem5_untracked_archive_sha256)" == "none" ]] || \
    fail "empty gem5 untracked state did not record archive hash none"
grep -Fq "gem5_source_fingerprint=${FIXTURE_GEM5_FINGERPRINT}" \
    "${VALID_PATCH}/binary-provenance.txt" || \
    fail "binary provenance lacks the validated gem5 source fingerprint"
grep -Fq 'strict_acceptance=0' "${VALID_ARTIFACT}/runner-invocation.txt" || \
    fail "default producer invocation does not record strict_acceptance=0"
grep -Fxq 'passthrough_args=' "${VALID_ARTIFACT}/runner-invocation.txt" || \
    fail "空 passthrough 没有序列化为零个参数"

for failure_case in archive-compile-failure archive-open-boundary; do
    if run_archive_failure_case "$failure_case"; then
        fail "${failure_case} fixture 意外返回成功"
    fi
    failure_artifact="${PRODUCER_ROOT}/artifacts/${failure_case}"
    [[ -f "${failure_artifact}/runner-metadata.txt" ]] || \
        fail "${failure_case} 未归档 runner metadata"
    [[ -f "${failure_artifact}/verdict.json" ]] || {
        sed -n '1,240p' "${PRODUCER_ROOT}/${failure_case}.log" >&2
        fail "${failure_case} 未归档 FAIL verdict"
    }
    [[ -f "${failure_artifact}/matrix.tsv" ]] || \
        fail "${failure_case} 未归档单行 matrix"
    grep -Fxq 'cleanup_status=verified' \
        "${failure_artifact}/runner-metadata.txt" || \
        fail "${failure_case} 未完成可证明 cleanup"
    grep -Fxq 'gem5_evidence_start_seq=' \
        "${failure_artifact}/runner-metadata.txt" || \
        fail "${failure_case} 未以空 BEGIN seq 归档失败"
    grep -Fxq 'gem5_evidence_end_seq=' \
        "${failure_artifact}/runner-metadata.txt" || \
        fail "${failure_case} 未以空 END seq 归档失败"
    python3 -c \
        'import json,sys; data=json.load(open(sys.argv[1])); assert data["outcome"] == "FAIL"; assert sys.argv[2] in data["reasons"]' \
        "${failure_artifact}/verdict.json" \
        "$([[ "$failure_case" == archive-compile-failure ]] && \
            printf compile_failure || printf nonzero_test_exit)" || \
        fail "${failure_case} verdict 缺少精确失败原因"
    awk -F '\t' 'NR == 2 {found = ($5 == "FAIL")} END {exit !found}' \
        "${failure_artifact}/matrix.tsv" || \
        fail "${failure_case} matrix 未记录 FAIL"
done
compile_evidence="${PRODUCER_ROOT}/artifacts/archive-compile-failure/gem5-evidence.tsv"
[[ "$(grep -c $'\ttest_begin\t' "$compile_evidence" || true)" -eq 0 && \
   "$(grep -c $'\ttest_end\t' "$compile_evidence" || true)" -eq 0 ]] || \
    fail "compile failure fixture 意外产生 boundary"
open_evidence="${PRODUCER_ROOT}/artifacts/archive-open-boundary/gem5-evidence.tsv"
[[ "$(grep -c $'\ttest_begin\t' "$open_evidence" || true)" -eq 1 && \
   "$(grep -c $'\ttest_end\t' "$open_evidence" || true)" -eq 0 ]] || \
    fail "open boundary fixture 未保留唯一 BEGIN 且缺少 END 的原始证据"

if run_producer_case producer-valid-passthrough \
        --gem5-debug ContractDebugFlag; then
    fail "非空 passthrough producer fixture 意外完成了 fake launch"
fi
PASSTHROUGH_ARTIFACT="${PRODUCER_ROOT}/artifacts/producer-valid-passthrough"
grep -Fq '[FAKE_LAUNCH_REACHED]' "${PASSTHROUGH_ARTIFACT}/qemu.log" || \
    fail "非空 passthrough producer fixture 未到达 launcher 边界"
grep -Fxq 'passthrough_args= --gem5-debug ContractDebugFlag' \
    "${PASSTHROUGH_ARTIFACT}/runner-invocation.txt" || \
    fail "非空 passthrough 的参数数量、顺序或值没有原样归档"

STRICT_DEBUG_FLAGS='HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo'
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-valid-strict \
        --gem5-debug "$STRICT_DEBUG_FLAGS"; then
    fail "strict producer fixture unexpectedly completed a fake launch"
fi
STRICT_VALID_ARTIFACT="${PRODUCER_ROOT}/artifacts/producer-valid-strict"
grep -Fq '[FAKE_LAUNCH_REACHED]' "${STRICT_VALID_ARTIFACT}/qemu.log" || \
    fail "clean strict provenance did not reach the launcher boundary"
grep -Fq 'strict_acceptance=1' \
    "${STRICT_VALID_ARTIFACT}/runner-invocation.txt" || \
    fail "strict producer invocation does not record strict_acceptance=1"

if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-missing-debug; then
    fail "strict producer 接受了缺失 GPU 执行证据 flags 的调用"
fi
grep -Fq 'strict acceptance requires --gem5-debug to include' \
    "${PRODUCER_ROOT}/producer-strict-missing-debug.log" || \
    fail "strict debug flag 拒绝缺少诊断"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-strict-missing-debug" ]] || \
    fail "strict debug flag 拒绝发生在 artifact 创建之后"

if COSIM_STRICT_ACCEPTANCE=invalid run_producer_case producer-invalid-strict; then
    fail "runner accepted an invalid COSIM_STRICT_ACCEPTANCE value"
fi
grep -Fq 'COSIM_STRICT_ACCEPTANCE must be 0 or 1' \
    "${PRODUCER_ROOT}/producer-invalid-strict.log" || \
    fail "invalid strict acceptance value lacked a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-invalid-strict" ]] || \
    fail "invalid strict acceptance value created a run artifact"

write_producer_metadata "0000000000000000000000000000000000000000"
if run_producer_case producer-bad-commit; then
    fail "runner accepted mismatched gem5 metadata commit"
fi
grep -Fq 'metadata commit does not match' \
    "${PRODUCER_ROOT}/producer-bad-commit.log" || \
    fail "commit mismatch did not produce a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-bad-commit/qemu.log" ]] || \
    fail "commit mismatch reached the launcher"

write_producer_metadata "$PRODUCER_GEM5_COMMIT" \
    "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
if run_producer_case producer-bad-fingerprint; then
    fail "runner accepted mismatched gem5 source fingerprint"
fi
grep -Fq 'source fingerprint does not match' \
    "${PRODUCER_ROOT}/producer-bad-fingerprint.log" || \
    fail "source fingerprint mismatch did not produce a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-bad-fingerprint/qemu.log" ]] || \
    fail "source fingerprint mismatch reached the launcher"

write_producer_metadata "$PRODUCER_GEM5_COMMIT" "$FIXTURE_GEM5_FINGERPRINT" \
    "${PRODUCER_ROOT}/gem5/build/VEGA_X86/other.opt"
if run_producer_case producer-bad-meta-path; then
    fail "runner accepted a noncanonical metadata binary"
fi
grep -Fq 'metadata binary is not canonical' \
    "${PRODUCER_ROOT}/producer-bad-meta-path.log" || \
    fail "metadata binary mismatch did not produce a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-bad-meta-path/qemu.log" ]] || \
    fail "metadata binary mismatch reached the launcher"

write_producer_metadata "$PRODUCER_GEM5_COMMIT" "$FIXTURE_GEM5_FINGERPRINT" \
    "$PRODUCER_GEM5_BIN" \
    "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
if run_producer_case producer-bad-hash; then
    fail "runner accepted a mismatched gem5 binary hash"
fi
grep -Fq 'metadata binary hash does not match' \
    "${PRODUCER_ROOT}/producer-bad-hash.log" || \
    fail "binary hash mismatch did not produce a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-bad-hash/qemu.log" ]] || \
    fail "binary hash mismatch reached the launcher"

write_producer_metadata
printf 'tampered fixture gem5 binary\n' > "$PRODUCER_GEM5_BIN"
chmod +x "$PRODUCER_GEM5_BIN"
PRODUCER_GEM5_SHA256="$(sha256sum "$PRODUCER_GEM5_BIN" | awk '{print $1}')"
write_producer_metadata
if run_producer_case producer-coordinated-binary; then
    fail "runner accepted a binary and metadata mutation not authorized by the tracked lock"
fi
grep -Fq 'baseline lock binary hash does not match' \
    "${PRODUCER_ROOT}/producer-coordinated-binary.log" || \
    fail "coordinated binary/metadata mutation lacked a baseline-lock diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-coordinated-binary/qemu.log" ]] || \
    fail "coordinated binary/metadata mutation reached the launcher"
printf 'fixture gem5 binary\n' > "$PRODUCER_GEM5_BIN"
chmod +x "$PRODUCER_GEM5_BIN"
PRODUCER_GEM5_SHA256="$(sha256sum "$PRODUCER_GEM5_BIN" | awk '{print $1}')"
write_producer_metadata

write_producer_lock "$PRODUCER_GEM5_COMMIT" "$FIXTURE_GEM5_FINGERPRINT" \
    "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
if run_producer_case producer-dirty-lock; then
    fail "runner accepted a working baseline lock inconsistent with metadata"
fi
grep -Fq 'baseline lock binary hash does not match' \
    "${PRODUCER_ROOT}/producer-dirty-lock.log" || \
    fail "working baseline-lock mismatch lacked a semantic diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-dirty-lock/qemu.log" ]] || \
    fail "semantically inconsistent baseline lock reached the launcher"
write_producer_lock

# 默认开发模式允许 working lock 内容语义一致但工作树 dirty；strict acceptance 还把
# 同一 lock 绑定到 tracked HEAD blob。
printf '\n' >> "$PRODUCER_GEM5_LOCK"
run_producer_case producer-replay-dirty-lock || true
grep -Fq '[FAKE_LAUNCH_REACHED]' \
    "${PRODUCER_ROOT}/artifacts/producer-replay-dirty-lock/qemu.log" || \
    fail "default replay mode rejected a semantically identical dirty lock"
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-dirty-lock \
        --gem5-debug "$STRICT_DEBUG_FLAGS"; then
    fail "strict acceptance accepted a baseline lock that differs from HEAD"
fi
grep -Fq 'baseline lock differs from HEAD' \
    "${PRODUCER_ROOT}/producer-strict-dirty-lock.log" || \
    fail "strict dirty-lock rejection lacked an anchored-lock diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-strict-dirty-lock/qemu.log" ]] || \
    fail "strict dirty baseline lock reached the launcher"
write_producer_lock

printf 'dirty fixture gem5 source\n' > "${PRODUCER_ROOT}/gem5/source.txt"
run_producer_case producer-replay-dirty-gem5 || true
grep -Fq '[FAKE_LAUNCH_REACHED]' \
    "${PRODUCER_ROOT}/artifacts/producer-replay-dirty-gem5/qemu.log" || \
    fail "default replay mode rejected a fingerprint-matched dirty gem5 tree"
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-dirty-gem5 \
        --gem5-debug "$STRICT_DEBUG_FLAGS"; then
    fail "strict acceptance accepted a dirty gem5 source tree"
fi
grep -Fq 'gem5 source tree must be clean before a strict acceptance run' \
    "${PRODUCER_ROOT}/producer-strict-dirty-gem5.log" || \
    fail "strict dirty gem5 source tree lacked a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-strict-dirty-gem5/qemu.log" ]] || \
    fail "strict dirty gem5 source tree reached the launcher"
printf 'fixture gem5 source\n' > "${PRODUCER_ROOT}/gem5/source.txt"

printf 'int vector_add_contract = 2;\n' > \
    "${PRODUCER_ROOT}/tests/kernels/vector_add.cpp"
run_producer_case producer-replay-dirty-top || true
grep -Fq '[FAKE_LAUNCH_REACHED]' \
    "${PRODUCER_ROOT}/artifacts/producer-replay-dirty-top/qemu.log" || \
    fail "default replay mode rejected a dirty top-level source tree"
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-dirty-top \
        --gem5-debug "$STRICT_DEBUG_FLAGS"; then
    fail "strict acceptance accepted a dirty top-level source tree"
fi
grep -Fq 'top-level source tree must be clean before a strict acceptance run' \
    "${PRODUCER_ROOT}/producer-strict-dirty-top.log" || \
    fail "strict dirty top-level source tree lacked a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-strict-dirty-top/qemu.log" ]] || \
    fail "strict dirty top-level source tree reached the launcher"
printf 'int vector_add_contract = 1;\n' > \
    "${PRODUCER_ROOT}/tests/kernels/vector_add.cpp"

if run_producer_case producer-wrong-runtime-image \
    --gem5-docker alternate-run:local; then
    fail "runner accepted a runtime image outside the tracked baseline"
fi
grep -Fq 'metadata Docker image does not match the runtime image' \
    "${PRODUCER_ROOT}/producer-wrong-runtime-image.log" || \
    fail "runtime image mismatch lacked a diagnostic"
[[ ! -e "${PRODUCER_ROOT}/artifacts/producer-wrong-runtime-image/qemu.log" ]] || \
    fail "runtime image mismatch reached the launcher"

write_producer_metadata
SCREEN_ARTIFACT="${PRODUCER_ROOT}/artifacts/producer-screen-log"
if PATH="${PRODUCER_FAKE_BIN}:${PATH}" COSIM_RUN_ID=producer-screen-log \
    "$PRODUCER_RUNNER" \
    --output-dir "$SCREEN_ARTIFACT" \
    --screen-log "${SCREEN_ARTIFACT}/alternate.log" vector_add \
    > "${PRODUCER_ROOT}/producer-screen-log.log" 2>&1; then
    fail "runner accepted a noncanonical screen log"
fi
grep -Fq -- '--screen-log must equal' \
    "${PRODUCER_ROOT}/producer-screen-log.log" || \
    fail "screen log mismatch did not produce a diagnostic"

OTHER_GEM5_BIN="${PRODUCER_ROOT}/gem5/build/VEGA_X86/other.opt"
printf 'other gem5 binary\n' > "$OTHER_GEM5_BIN"
chmod +x "$OTHER_GEM5_BIN"
if COSIM_RUN_ID=producer-other-binary "$PRODUCER_RUNNER" \
    --gem5-bin "$OTHER_GEM5_BIN" vector_add \
    > "${PRODUCER_ROOT}/producer-other-binary.log" 2>&1; then
    fail "runner accepted a noncanonical in-tree gem5 binary"
fi
grep -Fq -- '--gem5-bin must resolve to' \
    "${PRODUCER_ROOT}/producer-other-binary.log" || \
    fail "runner canonical binary rejection lacked a diagnostic"

if COSIM_RUN_ID=launcher-other-binary "$STRICT_LAUNCHER" \
    --gem5-bin "$OTHER_GEM5_BIN" \
    > "${PRODUCER_ROOT}/launcher-other-binary.log" 2>&1; then
    fail "launcher accepted a noncanonical in-tree gem5 binary"
fi
grep -Fq -- '--gem5-bin must resolve to' \
    "${PRODUCER_ROOT}/launcher-other-binary.log" || \
    fail "launcher canonical binary rejection lacked a diagnostic"

echo "[PASS] runner_contract"
