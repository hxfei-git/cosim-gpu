#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOST_RUNNER="${COSIM_DIR}/scripts/run_cosim_tests.sh"
LAUNCHER="${COSIM_DIR}/scripts/cosim_launch.sh"
PREFLIGHT="${COSIM_DIR}/scripts/cosim_preflight.sh"
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

# shellcheck disable=SC2016
launch_line="$(grep -nF 'setsid stdbuf -oL -eL "$LAUNCH_SCRIPT"' "$HOST_RUNNER" | cut -d: -f1)"
# shellcheck disable=SC2016
for needle in \
    '} > "${PATCH_DIR}/binary-provenance.txt"' \
    '} > "${RUNNER_ARTIFACT_DIR}/runner-invocation.txt"' \
    'cat >"$GUEST_SCRIPT_ARCHIVE" <<EOF'; do
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
grep -Fq -- '--share-dir|--artifact-dir)' "$HOST_RUNNER" || \
    fail "runner-owned launcher paths are not rejected"
# shellcheck disable=SC2016
grep -Fq 'cp -- "$GUEST_SCRIPT_ARCHIVE" "$GUEST_SCRIPT_HOST"' "$HOST_RUNNER" || \
    fail "Guest staging script is not copied from the archived script"
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
    "${COSIM_DIR}/scripts/cosim_guest_env.sh" "${PRODUCER_ROOT}/scripts/"
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
while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--artifact-dir" ]]; then
        artifact_dir="$2"
        shift 2
    else
        shift
    fi
done
[[ -n "$artifact_dir" ]] || exit 2
printf '%s\n' \
    'result=PASS' \
    'primary_category=infra_unknown' \
    'secondary_category=none' > "${artifact_dir}/cleanup-status.txt"
echo '[FAKE_LAUNCH_REACHED]'
exit 1
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

run_producer_case() {
    local case_name="$1"
    local strict_acceptance="${COSIM_STRICT_ACCEPTANCE:-0}"
    shift
    PATH="${PRODUCER_FAKE_BIN}:${PATH}" \
        COSIM_STRICT_ACCEPTANCE="$strict_acceptance" \
        COSIM_RUN_ID="$case_name" "$PRODUCER_RUNNER" \
        --session-name "contract-${case_name}" \
        --boot-timeout 1 \
        --output-dir "${PRODUCER_ROOT}/artifacts/${case_name}" \
        "$@" vector_add > "${PRODUCER_ROOT}/${case_name}.log" 2>&1
}

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
if run_producer_case producer-valid; then
    fail "producer fixture unexpectedly completed a fake launch"
fi
VALID_ARTIFACT="${PRODUCER_ROOT}/artifacts/producer-valid"
VALID_PATCH="${VALID_ARTIFACT}/patch"
grep -Fq '[FAKE_LAUNCH_REACHED]' "${VALID_ARTIFACT}/qemu.log" || \
    fail "valid producer provenance did not reach the launcher boundary"
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

if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-valid-strict; then
    fail "strict producer fixture unexpectedly completed a fake launch"
fi
STRICT_VALID_ARTIFACT="${PRODUCER_ROOT}/artifacts/producer-valid-strict"
grep -Fq '[FAKE_LAUNCH_REACHED]' "${STRICT_VALID_ARTIFACT}/qemu.log" || \
    fail "clean strict provenance did not reach the launcher boundary"
grep -Fq 'strict_acceptance=1' \
    "${STRICT_VALID_ARTIFACT}/runner-invocation.txt" || \
    fail "strict producer invocation does not record strict_acceptance=1"

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
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-dirty-lock; then
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
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-dirty-gem5; then
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
if COSIM_STRICT_ACCEPTANCE=1 run_producer_case producer-strict-dirty-top; then
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
