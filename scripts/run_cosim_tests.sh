#!/bin/bash
# Host-side single-operator test runner.
# Run one operator per QEMU + gem5 session to avoid cross-test state corruption.

set -euo pipefail

ORIGINAL_ARGS=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
TESTS_DIR="${COSIM_DIR}/tests"
KERNELS_DIR="${TESTS_DIR}/kernels"
LAUNCH_SCRIPT="${SCRIPT_DIR}/cosim_launch.sh"
BUILD_SCRIPT="${SCRIPT_DIR}/cosim_build.sh"
CANONICAL_GEM5_BIN="${COSIM_DIR}/gem5/build/VEGA_X86/gem5.opt"
GEM5_BUILD_META="${COSIM_DIR}/gem5/build/VEGA_X86/.cosim-build-meta"
GEM5_BASELINE_LOCK="${COSIM_DIR}/configs/cosim/gem5-baseline.lock"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/cosim_lib.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/cosim_guest_env.sh"

SESSION_NAME="${SESSION_NAME:-qemu-cosim-tests}"
SCREEN_LOG="${SCREEN_LOG:-}"
BOOT_TIMEOUT_SECS="${BOOT_TIMEOUT_SECS:-240}"
TEST_TIMEOUT_SECS="${TEST_TIMEOUT_SECS:-60}"
GUEST_RUN_TIMEOUT_SECS="${GUEST_RUN_TIMEOUT_SECS:-1800}"
GUEST_TEST_PREFIX="${GUEST_TEST_PREFIX:-}"
KEEP_ALIVE_ON_SUCCESS=0
RUN_ALL=0
REPEAT_COUNT=0
FILTER=""
PASSTHROUGH_ARGS=()
SCREEN_LOG_SET=0
OUTPUT_DIR=""
SESSION_DIR=""
SESSION_FIFO=""
LAUNCH_PID=""
CONTROL_FD=""
EFFECTIVE_GEM5_BIN="$CANONICAL_GEM5_BIN"
EFFECTIVE_NUM_GPUS="1"
EFFECTIVE_NUM_CUS="40"
EFFECTIVE_HOST_MEM="8G"
EFFECTIVE_VRAM_SIZE="16GiB"
EFFECTIVE_GEM5_DEBUG=""
EFFECTIVE_GEM5_DOCKER_IMAGE="${GEM5_DOCKER_IMAGE:-gem5-run:local}"
STRICT_GEM5_DEBUG_FLAGS=(
    HSAPacketProcessor GPUCommandProc GPUDisp GPUKernelInfo
)
GUEST_BRIDGE_POLICY="artifact-local"
STRICT_ACCEPTANCE="${COSIM_STRICT_ACCEPTANCE:-0}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
# shellcheck disable=SC2317,SC2329
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "${CYAN}[STEP]${NC} $*"; }

load_gem5_build_metadata() {
    local metadata_file="$1"
    local key count value

    for key in commit source_fingerprint_algorithm source_fingerprint target \
               binary binary_sha256 docker_image; do
        count="$(awk -F= -v wanted="$key" \
            '$1 == wanted {count++} END {print count + 0}' "$metadata_file")"
        [[ "$count" -eq 1 ]] || \
            error "gem5 build metadata must contain exactly one ${key}: ${metadata_file}"
        value="$(awk -F= -v wanted="$key" \
            '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$metadata_file")"
        [[ -n "$value" ]] || error "empty gem5 build metadata field: ${key}"
        case "$key" in
            commit) GEM5_META_COMMIT="$value" ;;
            source_fingerprint_algorithm) GEM5_META_FINGERPRINT_ALGORITHM="$value" ;;
            source_fingerprint) GEM5_META_SOURCE_FINGERPRINT="$value" ;;
            target) GEM5_META_TARGET="$value" ;;
            binary) GEM5_META_BINARY="$value" ;;
            binary_sha256) GEM5_META_BINARY_SHA256="$value" ;;
            docker_image) GEM5_META_DOCKER_IMAGE="$value" ;;
        esac
    done
}

load_gem5_baseline_lock() {
    local lock_file="$1"
    local key count value populated_lines
    local -a keys=(schema gem5_commit source_fingerprint_algorithm \
        source_fingerprint binary_sha256 docker_image)

    populated_lines="$(awk 'NF {count++} END {print count + 0}' "$lock_file")"
    [[ "$populated_lines" -eq "${#keys[@]}" ]] || \
        error "gem5 baseline lock contains unexpected or missing fields: ${lock_file}"
    for key in "${keys[@]}"; do
        count="$(awk -F= -v wanted="$key" \
            '$1 == wanted {count++} END {print count + 0}' "$lock_file")"
        [[ "$count" -eq 1 ]] || \
            error "gem5 baseline lock must contain exactly one ${key}: ${lock_file}"
        value="$(awk -F= -v wanted="$key" \
            '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$lock_file")"
        [[ -n "$value" ]] || error "empty gem5 baseline lock field: ${key}"
        case "$key" in
            schema) GEM5_LOCK_SCHEMA="$value" ;;
            gem5_commit) GEM5_LOCK_COMMIT="$value" ;;
            source_fingerprint_algorithm) GEM5_LOCK_FINGERPRINT_ALGORITHM="$value" ;;
            source_fingerprint) GEM5_LOCK_SOURCE_FINGERPRINT="$value" ;;
            binary_sha256) GEM5_LOCK_BINARY_SHA256="$value" ;;
            docker_image) GEM5_LOCK_DOCKER_IMAGE="$value" ;;
        esac
    done
}

current_gem5_source_fingerprint() {
    bash -c 'source "$1"; source_fingerprint "$2"' \
        _ "$BUILD_SCRIPT" "${COSIM_DIR}/gem5"
}

usage() {
    cat <<EOF
Host-side single-operator cosim test runner.

Usage: $0 [options] <operator-filter>

Options:
  --all                  Run all operators, one fresh cosim session each
  --repeat N             Run the same operator N times (fresh session each)
  --keep-alive           Leave QEMU + gem5 running after a successful test
  --session-name NAME    detached session name (default: qemu-cosim-tests)
  --screen-log PATH      console log path (default: <artifact-directory>/qemu.log)
  --boot-timeout SECS    guest boot timeout (default: 240)
  --test-timeout SECS    per-test timeout inside guest (default: 60)
  --guest-run-timeout S  host deadline for compile + test (default: 1800)
  --output-dir DIR        artifact directory below repository artifacts/
  -h, --help             Show this help

Unknown options are passed through to cosim_launch.sh.

Environment:
  GUEST_TEST_PREFIX       Empty, HSA_ENABLE_INTERRUPT=0, or
                          HSA_ENABLE_INTERRUPT=1 (default: empty -> 0)
  COSIM_STRICT_ACCEPTANCE 0：允许可重放的 dirty-tree 开发/诊断（默认）
                          1：strict v2 候选；要求顶层仓库与 gem5/ clean，
                            且 tracked baseline lock 与 HEAD 一致，并显式启用
                            HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)              RUN_ALL=1; shift ;;
        --repeat)           REPEAT_COUNT="$2"; shift 2 ;;
        --keep-alive)       KEEP_ALIVE_ON_SUCCESS=1; shift ;;
        --session-name)     SESSION_NAME="$2"; shift 2 ;;
        --screen-log)       SCREEN_LOG="$2"; SCREEN_LOG_SET=1; shift 2 ;;
        --boot-timeout)     BOOT_TIMEOUT_SECS="$2"; shift 2 ;;
        --test-timeout)     TEST_TIMEOUT_SECS="$2"; shift 2 ;;
        --guest-run-timeout) GUEST_RUN_TIMEOUT_SECS="$2"; shift 2 ;;
        --output-dir)       OUTPUT_DIR="$2"; shift 2 ;;
        -h|--help)          usage ;;
        --share-dir|--artifact-dir)
            error "$1 is runner-owned and cannot be passed through"
            ;;
        --*)
            [[ $# -ge 2 ]] || error "missing value for option: $1"
            PASSTHROUGH_ARGS+=("$1" "$2")
            shift 2
            ;;
        *)                  FILTER="$1"; shift ;;
    esac
done

[[ "$OUTPUT_DIR" != *$'\n'* && "$OUTPUT_DIR" != *$'\r'* && \
   "$OUTPUT_DIR" != *$'\t'* ]] || \
    error "control whitespace is not allowed in --output-dir"

for timeout_name in BOOT_TIMEOUT_SECS TEST_TIMEOUT_SECS GUEST_RUN_TIMEOUT_SECS; do
    timeout_value="${!timeout_name}"
    [[ "$timeout_value" =~ ^[1-9][0-9]*$ ]] || \
        error "${timeout_name} must be a positive integer"
done
[[ "$REPEAT_COUNT" =~ ^[0-9]+$ ]] || error "--repeat must be a non-negative integer"
[[ "$STRICT_ACCEPTANCE" == "0" || "$STRICT_ACCEPTANCE" == "1" ]] || \
    error "COSIM_STRICT_ACCEPTANCE must be 0 or 1"
export COSIM_STRICT_ACCEPTANCE="$STRICT_ACCEPTANCE"
if ! GUEST_HSA_ENABLE_INTERRUPT="$(cosim_guest_hsa_interrupt "$GUEST_TEST_PREFIX")"; then
    error "invalid GUEST_TEST_PREFIX"
fi
RECORDED_GUEST_TEST_PREFIX="$GUEST_TEST_PREFIX"
if [[ -z "$RECORDED_GUEST_TEST_PREFIX" ]]; then
    RECORDED_GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=${GUEST_HSA_ENABLE_INTERRUPT}"
fi

for ((arg_index=0; arg_index<${#PASSTHROUGH_ARGS[@]}; arg_index+=2)); do
    option="${PASSTHROUGH_ARGS[$arg_index]}"
    value="${PASSTHROUGH_ARGS[$((arg_index + 1))]}"
    case "$option" in
        --gem5-bin) EFFECTIVE_GEM5_BIN="$(realpath -m -- "$value")" ;;
        --num-gpus) EFFECTIVE_NUM_GPUS="$value" ;;
        --num-cus) EFFECTIVE_NUM_CUS="$value" ;;
        --host-mem) EFFECTIVE_HOST_MEM="$value" ;;
        --vram-size) EFFECTIVE_VRAM_SIZE="$value" ;;
        --gem5-debug) EFFECTIVE_GEM5_DEBUG="$value" ;;
        --gem5-docker) EFFECTIVE_GEM5_DOCKER_IMAGE="$value" ;;
    esac
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* && "$value" != *$'\t'* ]] || \
        error "control whitespace is not allowed in passthrough values"
done
if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
    for required_debug_flag in "${STRICT_GEM5_DEBUG_FLAGS[@]}"; do
        [[ ",${EFFECTIVE_GEM5_DEBUG}," == *",${required_debug_flag},"* ]] || \
            error "strict acceptance requires --gem5-debug to include ${required_debug_flag}"
    done
fi
[[ -x "$CANONICAL_GEM5_BIN" && ! -L "$CANONICAL_GEM5_BIN" ]] || \
    error "canonical gem5 binary is missing, non-executable, or symlinked: ${CANONICAL_GEM5_BIN}"
CANONICAL_GEM5_REALPATH="$(realpath -e -- "$CANONICAL_GEM5_BIN")"
[[ "$CANONICAL_GEM5_REALPATH" == "$CANONICAL_GEM5_BIN" ]] || \
    error "canonical gem5 binary resolves outside its fixed path: ${CANONICAL_GEM5_BIN}"
REQUESTED_GEM5_BIN="$EFFECTIVE_GEM5_BIN"
if ! EFFECTIVE_GEM5_BIN="$(realpath -e -- "$REQUESTED_GEM5_BIN")"; then
    error "gem5 not found: $REQUESTED_GEM5_BIN"
fi
[[ "$EFFECTIVE_GEM5_BIN" == "$CANONICAL_GEM5_REALPATH" ]] || \
    error "--gem5-bin must resolve to ${CANONICAL_GEM5_BIN}"
GEM5_CONFIG_ARGS="defaults:num-gpus=${EFFECTIVE_NUM_GPUS},num-cus=${EFFECTIVE_NUM_CUS},host-mem=${EFFECTIVE_HOST_MEM},vram-size=${EFFECTIVE_VRAM_SIZE}"
if [[ -n "$EFFECTIVE_GEM5_DEBUG" ]]; then
    GEM5_CONFIG_ARGS+=";debug-flags=${EFFECTIVE_GEM5_DEBUG}"
fi
RUNNER_MODE="pure_test"
if [[ "$KEEP_ALIVE_ON_SUCCESS" -eq 1 ]]; then
    RUNNER_MODE="keep_alive_diagnostic"
fi
RUNNER_TIMEOUT_POLICY="fixed-${TEST_TIMEOUT_SECS}"

COSIM_RUN_ID="${COSIM_RUN_ID:-$(generate_run_id)}"
export COSIM_RUN_ID

[[ "$COSIM_RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
    error "unsafe COSIM_RUN_ID: $COSIM_RUN_ID"
[[ "$COSIM_RUN_ID" != *..* ]] || error "unsafe COSIM_RUN_ID: $COSIM_RUN_ID"
[[ "$SESSION_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || \
    error "unsafe session name: $SESSION_NAME"
[[ "$SESSION_NAME" != *..* ]] || error "unsafe session name: $SESSION_NAME"

if [[ "$REPEAT_COUNT" -gt 0 && "$KEEP_ALIVE_ON_SUCCESS" -eq 1 ]]; then
    error "--keep-alive and --repeat cannot be used together"
fi
if [[ "$REPEAT_COUNT" -gt 0 && "$RUN_ALL" -eq 1 ]]; then
    error "--all and --repeat cannot be used together"
fi
if [[ "$SCREEN_LOG_SET" -eq 1 && \
      ( "$REPEAT_COUNT" -gt 0 || "$RUN_ALL" -eq 1 ) ]]; then
    error "--screen-log is fixed per fresh child artifact and cannot be used with --repeat or --all"
fi

SESSION_DIR="$(cosim_session_dir "$COSIM_RUN_ID" "$SESSION_NAME")"
SESSION_FIFO="${SESSION_DIR}/console.in"

# ---- Repeat mode: run same operator N times with fresh sessions ----

if [[ "$REPEAT_COUNT" -gt 0 && "$RUN_ALL" -eq 0 ]]; then
    [[ -n "$FILTER" ]] || { echo "Usage: $0 --repeat N <operator>"; exit 1; }

    [[ "$FILTER" =~ ^[a-z0-9_]+$ ]] || error "invalid operator name: $FILTER"
    [[ -f "${KERNELS_DIR}/${FILTER}.cpp" ]] || error "No operator named '${FILTER}'"
    REPEAT_OPERATOR="$FILTER"

    REPEAT_PASSED=0
    REPEAT_FAILED=0
    REPEAT_INFRA_FAIL=0
    declare -a REPEAT_MATRIX=()

    # shellcheck disable=SC2317
    repeat_partial_summary() {
        echo ""
        echo "============================================================"
        echo "  Repeat-Run Partial Summary (interrupted)"
        echo "  Operator: $FILTER"
        echo "  Completed: $((REPEAT_PASSED + REPEAT_FAILED)) / $REPEAT_COUNT"
        echo "  Passed: $REPEAT_PASSED  Failed: $REPEAT_FAILED  Infra failures: $REPEAT_INFRA_FAIL"
        echo "============================================================"
        for entry in "${REPEAT_MATRIX[@]}"; do
            echo "  $entry"
        done
        echo "============================================================"
    }
    trap 'repeat_partial_summary; exit 130' INT TERM

    for ((i=1; i<=REPEAT_COUNT; i++)); do
        iter_run_id="$(generate_run_id)"
        sub_session="${SESSION_NAME}-repeat-${i}"
        if [[ -n "$OUTPUT_DIR" ]]; then
            iter_artifact_dir="${OUTPUT_DIR%/}/repeat-${i}-${iter_run_id}"
        else
            iter_artifact_dir="$(cosim_artifact_dir "$COSIM_DIR" "$REPEAT_OPERATOR" "$iter_run_id")"
        fi

        step "Repeat iteration $i/$REPEAT_COUNT (run-ID: $iter_run_id)"

        iter_category_file="${iter_artifact_dir}/runner-category.txt"
        if COSIM_RUN_ID="$iter_run_id" COSIM_CATEGORY_FILE="$iter_category_file" "$0" \
            --session-name "$sub_session" \
            --boot-timeout "$BOOT_TIMEOUT_SECS" \
            --test-timeout "$TEST_TIMEOUT_SECS" \
            --guest-run-timeout "$GUEST_RUN_TIMEOUT_SECS" \
            --output-dir "$iter_artifact_dir" \
            "${PASSTHROUGH_ARGS[@]}" \
            "$FILTER"; then
            run_rc=0
            category="$COSIM_CAT_TEST_PASS"
        else
            run_rc=$?
            category="$COSIM_CAT_INFRA_UNKNOWN"
        fi
        if [[ -f "$iter_category_file" ]]; then
            category="$(cat "$iter_category_file")"
        fi

        if [[ "$run_rc" -eq 0 ]]; then
            REPEAT_PASSED=$((REPEAT_PASSED + 1))
        else
            REPEAT_FAILED=$((REPEAT_FAILED + 1))
            if is_infra_failure "$category"; then
                REPEAT_INFRA_FAIL=$((REPEAT_INFRA_FAIL + 1))
            fi
        fi
        if [[ -d "$iter_artifact_dir" ]]; then
            actual_artifact_dir="$iter_artifact_dir"
        elif [[ -d "${COSIM_DIR}/artifacts/standalone/${iter_run_id}" ]]; then
            actual_artifact_dir="${COSIM_DIR}/artifacts/standalone/${iter_run_id}"
        else
            actual_artifact_dir="(none)"
        fi
        REPEAT_MATRIX+=("$i | $iter_run_id | $category | exit=$run_rc | artifacts=${actual_artifact_dir}")
    done

    echo ""
    echo "============================================================"
    echo "  Repeat-Run Results"
    echo "  Operator: $FILTER"
    echo "  Total: $REPEAT_COUNT  Passed: $REPEAT_PASSED  Failed: $REPEAT_FAILED  Infra failures: $REPEAT_INFRA_FAIL"
    echo "============================================================"
    echo "  # | Run-ID | Category | Exit | Artifacts"
    echo "  --|--------|----------|------|----------"
    for entry in "${REPEAT_MATRIX[@]}"; do
        echo "  $entry"
    done
    echo "============================================================"

    if [[ "$REPEAT_INFRA_FAIL" -gt 0 ]]; then
        exit 1
    fi
    [[ "$REPEAT_FAILED" -eq 0 ]]
    exit $?
fi

if [[ "$RUN_ALL" -eq 1 ]]; then
    mapfile -t ALL_TESTS < <(find "$KERNELS_DIR" -maxdepth 1 -type f -name '*.cpp' -printf '%f\n' | sed 's/\.cpp$//' | sort)
    [[ ${#ALL_TESTS[@]} -gt 0 ]] || error "No operators found in ${KERNELS_DIR}"

    PASSED=0
    FAILED=0

    for test_name in "${ALL_TESTS[@]}"; do
        sub_session="${SESSION_NAME}-${test_name}"
        child_run_id="$(generate_run_id)"
        if [[ -n "$OUTPUT_DIR" ]]; then
            child_output_dir="${OUTPUT_DIR%/}/${test_name}-${child_run_id}"
        else
            child_output_dir="$(cosim_artifact_dir "$COSIM_DIR" "$test_name" "$child_run_id")"
        fi
        if COSIM_RUN_ID="$child_run_id" "$0" \
            --session-name "$sub_session" \
            --boot-timeout "$BOOT_TIMEOUT_SECS" \
            --test-timeout "$TEST_TIMEOUT_SECS" \
            --guest-run-timeout "$GUEST_RUN_TIMEOUT_SECS" \
            --output-dir "$child_output_dir" \
            "${PASSTHROUGH_ARGS[@]}" \
            "$test_name"; then
            run_rc=0
        else
            run_rc=$?
        fi

        if [[ "$run_rc" -eq 0 ]]; then
            PASSED=$((PASSED + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    done

    echo "============================================================"
    echo "  Fresh-session Results: ${PASSED}/${#ALL_TESTS[@]} passed, ${FAILED} failed"
    echo "============================================================"
    if [[ "$FAILED" -ne 0 ]]; then
        exit 1
    fi
    exit 0
fi

[[ -n "$FILTER" ]] || usage

cleanup_session() {
    local launcher_was_started=0
    local fallback_cleanup_completed=0
    [[ -n "${LAUNCH_PID:-}" ]] && launcher_was_started=1
    if [[ -n "${LAUNCH_PID:-}" ]] && kill -0 "$LAUNCH_PID" 2>/dev/null; then
        kill -TERM -- "-${LAUNCH_PID}" >/dev/null 2>&1 || true
        local wait_count=0
        while kill -0 "$LAUNCH_PID" 2>/dev/null && [[ $wait_count -lt 15 ]]; do
            sleep 1
            wait_count=$((wait_count + 1))
        done
        if kill -0 "$LAUNCH_PID" 2>/dev/null; then
            kill -KILL -- "-${LAUNCH_PID}" >/dev/null 2>&1 || true
        fi
    fi
    if [[ -n "${LAUNCH_PID:-}" ]]; then
        wait "$LAUNCH_PID" 2>/dev/null || true
    fi

    local launcher_manifest="/tmp/cosim-${COSIM_RUN_ID}.session/resources.manifest"
    if [[ -f "$launcher_manifest" ]]; then
        if ! "${SCRIPT_DIR}/cosim_cleanup.sh" --run-id "$COSIM_RUN_ID" \
            --manifest "$launcher_manifest" --confirm; then
            record_category "$COSIM_CAT_CLEANUP_FAIL"
            return 1
        fi
        fallback_cleanup_completed=1
    fi

    if [[ -f "${RUNNER_ARTIFACT_DIR}/cleanup-status.txt" ]]; then
        grep -qx 'result=PASS' "${RUNNER_ARTIFACT_DIR}/cleanup-status.txt" || {
            record_category "$COSIM_CAT_CLEANUP_FAIL"
            return 1
        }
    elif [[ "$launcher_was_started" -eq 1 && "$fallback_cleanup_completed" -eq 0 ]]; then
        record_category "$COSIM_CAT_CLEANUP_FAIL"
        return 1
    fi

    # This directory contains only runner-owned control files.
    if [[ -d "$SESSION_DIR" && ! -L "$SESSION_DIR" ]]; then
        rm -f -- "$SESSION_FIFO" "${SESSION_DIR}/launcher.pid"
        rmdir -- "$SESSION_DIR"
    fi
}

session_alive() {
    [[ -n "${LAUNCH_PID:-}" ]] && kill -0 "$LAUNCH_PID" 2>/dev/null
}

send_guest() {
    local line="$1"
    printf '%s\n' "$line" >&$CONTROL_FD
}

record_category() {
    local cat="$1"
    local use_launcher="${2:-false}"
    if [[ "$use_launcher" == "true" ]]; then
        local launcher_cat_file="${RUNNER_ARTIFACT_DIR}/launcher-category.txt"
        if [[ -f "$launcher_cat_file" ]]; then
            local launcher_cat
            launcher_cat="$(cat "$launcher_cat_file")"
            if [[ -n "$launcher_cat" && "$launcher_cat" != "$COSIM_CAT_TEST_PASS" ]]; then
                cat="$launcher_cat"
            fi
        fi
    fi
    local category_file="${COSIM_CATEGORY_FILE:-${RUNNER_ARTIFACT_DIR}/runner-category.txt}"
    category_file="$(realpath -m -- "$category_file")"
    case "$category_file" in
        "${RUNNER_ARTIFACT_DIR}/"*) printf '%s\n' "$cat" > "$category_file" ;;
        *) return 1 ;;
    esac
}

# shellcheck disable=SC2317,SC2329
on_interrupt() {
    echo ""
    if [[ "$KEEP_ALIVE_ON_SUCCESS" -eq 1 ]]; then
        warn "Interrupted. Session preserved (--keep-alive)."
        warn "Launcher PID: ${LAUNCH_PID:-unknown}"
        warn "Console log: ${SCREEN_LOG}"
        warn "Console pipe: ${SESSION_FIFO}"
        warn "安全恢复：先验证并终止本次 launcher process group，确认退出后才允许精确 manifest fallback。"
        warn "禁止对 live launcher 直接运行 cosim_cleanup.sh；参见 cosim-gpu-debug 的 live-wait-state 流程。"
    else
        warn "Interrupted. Cleaning up session..."
        cleanup_session
    fi
    exit 130
}

# shellcheck disable=SC2317
on_exit() {
    local rc=$?
    if [[ $rc -ne 0 && "$KEEP_ALIVE_ON_SUCCESS" -eq 0 ]]; then
        cleanup_session
    fi
}

trap on_interrupt INT TERM
trap on_exit EXIT

match_test() {
    [[ "$FILTER" =~ ^[a-z0-9_]+$ ]] || error "invalid operator name: $FILTER"
    [[ -f "${KERNELS_DIR}/${FILTER}.cpp" ]] || error "No operator named '${FILTER}'"
    printf '%s\n' "$FILTER"
}

TEST_NAME="$(match_test)"
GUEST_SCRIPT=".cosim_guest_run.${COSIM_RUN_ID}.${TEST_NAME}.sh"
TOKEN_RUN_SHA256="$(printf '%s' "$COSIM_RUN_ID" | sha256sum | awk '{print $1}')"
[[ "$TOKEN_RUN_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
    error "failed to derive completion token identity from COSIM_RUN_ID"
TOKEN="COSIM_TEST_DONE_${TEST_NAME}_${TOKEN_RUN_SHA256}"
COMPILE_TOKEN="COSIM_COMPILE_DONE_${TEST_NAME}_${TOKEN_RUN_SHA256}"

if [[ -n "$OUTPUT_DIR" ]]; then
    RUNNER_ARTIFACT_DIR="$(realpath -m -- "$OUTPUT_DIR")"
else
    RUNNER_ARTIFACT_DIR="$(cosim_artifact_dir "$COSIM_DIR" "$TEST_NAME" "$COSIM_RUN_ID")"
fi
case "$RUNNER_ARTIFACT_DIR" in
    "${COSIM_DIR}/artifacts/"*) ;;
    *) error "--output-dir must be below ${COSIM_DIR}/artifacts" ;;
esac
[[ ! -L "$RUNNER_ARTIFACT_DIR" ]] || error "artifact directory must not be a symlink"
if [[ -d "$RUNNER_ARTIFACT_DIR" ]] && \
   find "$RUNNER_ARTIFACT_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    error "artifact directory must be empty: $RUNNER_ARTIFACT_DIR"
fi
mkdir -p "$RUNNER_ARTIFACT_DIR"

CANONICAL_SCREEN_LOG="${RUNNER_ARTIFACT_DIR}/qemu.log"
if [[ "$SCREEN_LOG_SET" -eq 1 ]]; then
    SCREEN_LOG="$(realpath -m -- "$SCREEN_LOG")"
    [[ "$SCREEN_LOG" == "$CANONICAL_SCREEN_LOG" ]] || \
        error "--screen-log must equal ${CANONICAL_SCREEN_LOG}"
else
    SCREEN_LOG="$CANONICAL_SCREEN_LOG"
fi

STAGING_DIR="${RUNNER_ARTIFACT_DIR}/staging"
[[ ! -e "$STAGING_DIR" && ! -L "$STAGING_DIR" ]] || error "staging path already exists"
mkdir -p "$STAGING_DIR"
rsync -a --exclude build/ --exclude '.cosim_guest_run.*' \
    "${TESTS_DIR}/" "${STAGING_DIR}/"
GUEST_SCRIPT_HOST="${STAGING_DIR}/${GUEST_SCRIPT}"
GUEST_SCRIPT_ARCHIVE="${RUNNER_ARTIFACT_DIR}/guest-run.sh"

PATCH_DIR="${RUNNER_ARTIFACT_DIR}/patch"
mkdir -p "$PATCH_DIR"

git -C "$COSIM_DIR" status --short --untracked-files=all > \
    "${PATCH_DIR}/repo-status.txt"
git -C "$COSIM_DIR" diff --binary --no-ext-diff HEAD > \
    "${PATCH_DIR}/repo.patch"
git -C "$COSIM_DIR" ls-files --others --exclude-standard > \
    "${PATCH_DIR}/repo-untracked-files.txt"
if [[ -s "${PATCH_DIR}/repo-untracked-files.txt" ]]; then
    git -C "$COSIM_DIR" ls-files -z --others --exclude-standard | \
        tar -C "$COSIM_DIR" --null --files-from=- -cf \
            "${PATCH_DIR}/repo-untracked-files.tar"
fi

SOURCE_FINGERPRINT="$(
    cd "$STAGING_DIR"
    find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
)"

git -C "${COSIM_DIR}/gem5" -c status.renames=false \
    status --porcelain=v1 --untracked-files=all --ignore-submodules=none > \
    "${PATCH_DIR}/gem5-status.txt"
git -C "${COSIM_DIR}/gem5" diff --binary --no-ext-diff HEAD > \
    "${PATCH_DIR}/gem5.patch"
git -C "${COSIM_DIR}/gem5" ls-files --others --exclude-standard > \
    "${PATCH_DIR}/untracked-files.txt"
if [[ -s "${PATCH_DIR}/untracked-files.txt" ]]; then
    git -C "${COSIM_DIR}/gem5" ls-files -z --others --exclude-standard | \
        tar -C "${COSIM_DIR}/gem5" --null --files-from=- -cf \
            "${PATCH_DIR}/untracked-files.tar"
fi

[[ -f "$GEM5_BUILD_META" && ! -L "$GEM5_BUILD_META" ]] || \
    error "canonical gem5 build metadata is missing or symlinked: ${GEM5_BUILD_META}"
GEM5_BUILD_META_ARCHIVE="${PATCH_DIR}/gem5-build-meta.txt"
cp -- "$GEM5_BUILD_META" "$GEM5_BUILD_META_ARCHIVE"
GEM5_BUILD_META_SHA256="$(sha256sum "$GEM5_BUILD_META" | awk '{print $1}')"
[[ "$(sha256sum "$GEM5_BUILD_META_ARCHIVE" | awk '{print $1}')" == \
   "$GEM5_BUILD_META_SHA256" ]] || error "archived gem5 build metadata hash mismatch"

GEM5_BASELINE_LOCK_REL="configs/cosim/gem5-baseline.lock"
if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
    git -C "$COSIM_DIR" ls-files --error-unmatch -- "$GEM5_BASELINE_LOCK_REL" \
        >/dev/null 2>&1 || error "gem5 baseline lock is not tracked: ${GEM5_BASELINE_LOCK}"
    git -C "$COSIM_DIR" diff --quiet HEAD -- "$GEM5_BASELINE_LOCK_REL" || \
        error "gem5 baseline lock differs from HEAD: ${GEM5_BASELINE_LOCK}"
fi
[[ -f "$GEM5_BASELINE_LOCK" && ! -L "$GEM5_BASELINE_LOCK" ]] || \
    error "gem5 baseline lock is missing or symlinked: ${GEM5_BASELINE_LOCK}"
GEM5_BASELINE_LOCK_ARCHIVE="${PATCH_DIR}/gem5-baseline.lock"
cp -- "$GEM5_BASELINE_LOCK" "$GEM5_BASELINE_LOCK_ARCHIVE"
GEM5_BASELINE_LOCK_SHA256="$(sha256sum "$GEM5_BASELINE_LOCK" | awk '{print $1}')"
[[ "$(sha256sum "$GEM5_BASELINE_LOCK_ARCHIVE" | awk '{print $1}')" == \
   "$GEM5_BASELINE_LOCK_SHA256" ]] || error "archived gem5 baseline lock hash mismatch"
if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
    GEM5_BASELINE_LOCK_HEAD_SHA256="$(git -C "$COSIM_DIR" \
        show "HEAD:${GEM5_BASELINE_LOCK_REL}" | sha256sum | awk '{print $1}')"
    [[ "$GEM5_BASELINE_LOCK_SHA256" == "$GEM5_BASELINE_LOCK_HEAD_SHA256" ]] || \
        error "archived gem5 baseline lock does not match its top-level HEAD blob"
fi

GEM5_CURRENT_COMMIT="$(git -C "${COSIM_DIR}/gem5" rev-parse HEAD)"
GEM5_CURRENT_SUBJECT="$(git -C "${COSIM_DIR}/gem5" log -1 --format=%s)"
GEM5_CURRENT_BINARY_SHA256="$(sha256sum "$EFFECTIVE_GEM5_BIN" | awk '{print $1}')"
if ! GEM5_CURRENT_DOCKER_IMAGE_ID="$(docker image inspect --format '{{.Id}}' \
    "$EFFECTIVE_GEM5_DOCKER_IMAGE" 2>/dev/null)"; then
    error "gem5 runtime Docker image is unavailable: ${EFFECTIVE_GEM5_DOCKER_IMAGE}"
fi
[[ "$GEM5_CURRENT_DOCKER_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || \
    error "invalid gem5 runtime Docker image identity: ${GEM5_CURRENT_DOCKER_IMAGE_ID}"
if ! GEM5_CURRENT_SOURCE_FINGERPRINT="$(current_gem5_source_fingerprint)"; then
    error "failed to compute the current gem5 source fingerprint"
fi
[[ "$GEM5_CURRENT_SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]] || \
    error "invalid current gem5 source fingerprint: ${GEM5_CURRENT_SOURCE_FINGERPRINT}"

{
    echo "head_commit=$(git -C "$COSIM_DIR" rev-parse HEAD)"
    echo "source_fingerprint=${SOURCE_FINGERPRINT}"
    echo "program=${TEST_NAME}"
    echo "runner_sha256=$(sha256sum "${SCRIPT_DIR}/run_cosim_tests.sh" | awk '{print $1}')"
    echo "guest_env_helper_sha256=$(sha256sum "${SCRIPT_DIR}/cosim_guest_env.sh" | awk '{print $1}')"
    echo "launcher_sha256=$(sha256sum "$LAUNCH_SCRIPT" | awk '{print $1}')"
    echo "build_script_sha256=$(sha256sum "$BUILD_SCRIPT" | awk '{print $1}')"
    echo "repo_status_sha256=$(sha256sum "${PATCH_DIR}/repo-status.txt" | awk '{print $1}')"
    echo "repo_patch_sha256=$(sha256sum "${PATCH_DIR}/repo.patch" | awk '{print $1}')"
    echo "repo_untracked_list_sha256=$(sha256sum "${PATCH_DIR}/repo-untracked-files.txt" | awk '{print $1}')"
    if [[ -f "${PATCH_DIR}/repo-untracked-files.tar" ]]; then
        echo "repo_untracked_archive_sha256=$(sha256sum "${PATCH_DIR}/repo-untracked-files.tar" | awk '{print $1}')"
    else
        echo "repo_untracked_archive_sha256=none"
    fi
    echo "gem5_source_commit=${GEM5_CURRENT_COMMIT}"
    echo "gem5_source_fingerprint=${GEM5_CURRENT_SOURCE_FINGERPRINT}"
    echo "gem5_status_sha256=$(sha256sum "${PATCH_DIR}/gem5-status.txt" | awk '{print $1}')"
    echo "gem5_patch_sha256=$(sha256sum "${PATCH_DIR}/gem5.patch" | awk '{print $1}')"
    echo "gem5_untracked_list_sha256=$(sha256sum "${PATCH_DIR}/untracked-files.txt" | awk '{print $1}')"
    if [[ -f "${PATCH_DIR}/untracked-files.tar" ]]; then
        echo "gem5_untracked_archive_sha256=$(sha256sum "${PATCH_DIR}/untracked-files.tar" | awk '{print $1}')"
    else
        echo "gem5_untracked_archive_sha256=none"
    fi
    echo "gem5_build_meta_sha256=${GEM5_BUILD_META_SHA256}"
    echo "gem5_baseline_lock_sha256=${GEM5_BASELINE_LOCK_SHA256}"
} > "${PATCH_DIR}/source-snapshot.txt"

load_gem5_build_metadata "$GEM5_BUILD_META_ARCHIVE"
load_gem5_baseline_lock "$GEM5_BASELINE_LOCK_ARCHIVE"
if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
    [[ ! -s "${PATCH_DIR}/repo-status.txt" ]] || \
        error "top-level source tree must be clean before a strict acceptance run"
    [[ ! -s "${PATCH_DIR}/gem5-status.txt" ]] || \
        error "gem5 source tree must be clean before a strict acceptance run"
fi
[[ "$GEM5_META_COMMIT" =~ ^[0-9a-f]{40}$ && \
   "$GEM5_META_COMMIT" == "$GEM5_CURRENT_COMMIT" ]] || \
    error "gem5 build metadata commit does not match the current source tree"
[[ "$GEM5_META_FINGERPRINT_ALGORITHM" == "2" ]] || \
    error "unsupported gem5 source fingerprint algorithm: ${GEM5_META_FINGERPRINT_ALGORITHM}"
[[ "$GEM5_META_SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ && \
   "$GEM5_META_SOURCE_FINGERPRINT" == "$GEM5_CURRENT_SOURCE_FINGERPRINT" ]] || \
    error "gem5 build metadata source fingerprint does not match the current source tree"
[[ "$GEM5_META_TARGET" == "VEGA_X86" ]] || \
    error "gem5 build metadata target must be VEGA_X86"
[[ "$GEM5_META_BINARY" == "$CANONICAL_GEM5_BIN" ]] || \
    error "gem5 build metadata binary is not canonical: ${GEM5_META_BINARY}"
if ! GEM5_META_BINARY_REALPATH="$(realpath -e -- "$GEM5_META_BINARY")"; then
    error "gem5 build metadata binary does not exist: ${GEM5_META_BINARY}"
fi
[[ "$GEM5_META_BINARY_REALPATH" == "$EFFECTIVE_GEM5_BIN" ]] || \
    error "gem5 build metadata binary does not resolve to the selected binary"
[[ "$GEM5_META_BINARY_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$GEM5_META_BINARY_SHA256" == "$GEM5_CURRENT_BINARY_SHA256" ]] || \
    error "gem5 build metadata binary hash does not match the selected binary"
[[ "$GEM5_META_DOCKER_IMAGE" =~ ^sha256:[0-9a-f]{64}$ && \
   "$GEM5_META_DOCKER_IMAGE" == "$GEM5_CURRENT_DOCKER_IMAGE_ID" ]] || \
    error "gem5 build metadata Docker image does not match the runtime image"
[[ "$GEM5_LOCK_SCHEMA" == "1" ]] || \
    error "unsupported gem5 baseline lock schema: ${GEM5_LOCK_SCHEMA}"
[[ "$GEM5_LOCK_FINGERPRINT_ALGORITHM" == "2" ]] || \
    error "unsupported gem5 baseline lock source fingerprint algorithm"
[[ "$GEM5_LOCK_COMMIT" =~ ^[0-9a-f]{40}$ && \
   "$GEM5_LOCK_COMMIT" == "$GEM5_META_COMMIT" ]] || \
    error "gem5 baseline lock commit does not match build metadata"
[[ "$GEM5_LOCK_SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ && \
   "$GEM5_LOCK_SOURCE_FINGERPRINT" == "$GEM5_META_SOURCE_FINGERPRINT" ]] || \
    error "gem5 baseline lock source fingerprint does not match build metadata"
[[ "$GEM5_LOCK_BINARY_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$GEM5_LOCK_BINARY_SHA256" == "$GEM5_META_BINARY_SHA256" ]] || \
    error "gem5 baseline lock binary hash does not match build metadata"
[[ "$GEM5_LOCK_DOCKER_IMAGE" =~ ^sha256:[0-9a-f]{64}$ && \
   "$GEM5_LOCK_DOCKER_IMAGE" == "$GEM5_META_DOCKER_IMAGE" ]] || \
    error "gem5 baseline lock Docker image does not match build metadata"

{
    echo "gem5_source_commit=${GEM5_CURRENT_COMMIT}"
    echo "gem5_source_subject=${GEM5_CURRENT_SUBJECT}"
    echo "gem5_source_fingerprint_algorithm=2"
    echo "gem5_source_fingerprint=${GEM5_CURRENT_SOURCE_FINGERPRINT}"
    echo "gem5_binary=${EFFECTIVE_GEM5_BIN}"
    echo "gem5_sha256=${GEM5_CURRENT_BINARY_SHA256}"
    echo "gem5_build_meta=${GEM5_BUILD_META_ARCHIVE}"
    echo "gem5_build_meta_sha256=${GEM5_BUILD_META_SHA256}"
    echo "gem5_baseline_lock=${GEM5_BASELINE_LOCK_ARCHIVE}"
    echo "gem5_baseline_lock_sha256=${GEM5_BASELINE_LOCK_SHA256}"
    echo "gem5_docker_image_name=${EFFECTIVE_GEM5_DOCKER_IMAGE}"
    echo "gem5_docker_image=${GEM5_CURRENT_DOCKER_IMAGE_ID}"
} > "${PATCH_DIR}/binary-provenance.txt"

{
    echo "schema=cosim-runner-invocation/v1"
    echo "run_id=${COSIM_RUN_ID}"
    echo "program=${TEST_NAME}"
    echo "program_source=tests/kernels/${TEST_NAME}.cpp"
    echo "program_binary=tests/build/${TEST_NAME}"
    echo "runner_argument=${TEST_NAME}"
    echo "mode=${RUNNER_MODE}"
    echo "repeat_count=1"
    echo "timeout_policy=${RUNNER_TIMEOUT_POLICY}"
    echo "boot_timeout=${BOOT_TIMEOUT_SECS}"
    echo "test_timeout=${TEST_TIMEOUT_SECS}"
    echo "guest_run_timeout=${GUEST_RUN_TIMEOUT_SECS}"
    echo "guest_test_prefix=${RECORDED_GUEST_TEST_PREFIX}"
    echo "guest_test_prefix_input=${GUEST_TEST_PREFIX}"
    echo "expected_hsa_interrupt=${GUEST_HSA_ENABLE_INTERRUPT}"
    echo "gem5_binary=${EFFECTIVE_GEM5_BIN}"
    echo "gem5_docker_image_name=${EFFECTIVE_GEM5_DOCKER_IMAGE}"
    echo "gem5_docker_image=${GEM5_CURRENT_DOCKER_IMAGE_ID}"
    echo "gem5_config_args=${GEM5_CONFIG_ARGS}"
    echo "strict_acceptance=${STRICT_ACCEPTANCE}"
    echo "output_dir=${RUNNER_ARTIFACT_DIR}"
    echo "artifact_dir=${RUNNER_ARTIFACT_DIR}"
    echo "artifact_dir_pattern=-"
    echo "matrix_path=${RUNNER_ARTIFACT_DIR}/matrix.tsv"
    echo "provenance_file=${PATCH_DIR}/binary-provenance.txt"
    echo "guest_bridge_policy=${GUEST_BRIDGE_POLICY}"
    echo "guest_bridge_host=${STAGING_DIR}"
    echo "guest_bridge_guest=/mnt"
    echo "cwd=$(pwd -P)"
    printf 'argv0=%q\n' "$0"
    printf 'argv='
    cosim_print_shell_words "${ORIGINAL_ARGS[@]}"
    printf '\n'
    printf 'passthrough_args='
    cosim_print_shell_words "${PASSTHROUGH_ARGS[@]}"
    printf '\n'
} > "${RUNNER_ARTIFACT_DIR}/runner-invocation.txt"

if ! python3 -B "${SCRIPT_DIR}/cosim_log_evidence.py" render-guest-script \
        --program "$TEST_NAME" \
        --run-id "$COSIM_RUN_ID" \
        --hsa-enable-interrupt "$GUEST_HSA_ENABLE_INTERRUPT" \
        --test-timeout "$TEST_TIMEOUT_SECS" > "$GUEST_SCRIPT_ARCHIVE"; then
    error "failed to render canonical Guest script"
fi
chmod +x "$GUEST_SCRIPT_ARCHIVE"
cp -- "$GUEST_SCRIPT_ARCHIVE" "$GUEST_SCRIPT_HOST"

[[ ! -e "$SESSION_DIR" && ! -L "$SESSION_DIR" ]] || \
    error "stale or symlinked session directory exists: $SESSION_DIR"
mkdir -p "$SESSION_DIR"
[[ ! -e "$SCREEN_LOG" ]] || error "artifact log already exists: $SCREEN_LOG"
mkfifo "$SESSION_FIFO"
exec {CONTROL_FD}<>"$SESSION_FIFO"

step "[${TEST_NAME}] Starting detached QEMU + gem5 session..."
setsid stdbuf -oL -eL "$LAUNCH_SCRIPT" \
    --share-dir "$STAGING_DIR" \
    --artifact-dir "$RUNNER_ARTIFACT_DIR" \
    "${PASSTHROUGH_ARGS[@]}" \
    <&$CONTROL_FD >"$SCREEN_LOG" 2>&1 &
LAUNCH_PID=$!
echo "$LAUNCH_PID" >"${SESSION_DIR}/launcher.pid"
echo "$LAUNCH_PID" >"${RUNNER_ARTIFACT_DIR}/launcher.pid"

step "[${TEST_NAME}] Waiting for guest login prompt..."
start_ts=$(date +%s)
while true; do
    if [[ -f "$SCREEN_LOG" ]] && grep -a -q 'root@gem5:~#' "$SCREEN_LOG"; then
        info "[${TEST_NAME}] Guest shell is ready"
        break
    fi
    if ! session_alive; then
        rm -f "$GUEST_SCRIPT_HOST"
        record_category "$COSIM_CAT_QEMU_EXIT" "true"
        error "[${TEST_NAME}] detached session exited during boot. Log tail:\n$(tail -n 40 "$SCREEN_LOG" 2>/dev/null)"
    fi
    now_ts=$(date +%s)
    if (( now_ts - start_ts >= BOOT_TIMEOUT_SECS )); then
        record_category "$COSIM_CAT_BOOT_TIMEOUT" "true"
        error "[${TEST_NAME}] guest did not reach login prompt within ${BOOT_TIMEOUT_SECS}s"
    fi
    sleep 2
done

start_line=1
if [[ -f "$SCREEN_LOG" ]]; then
    start_line=$(( $(wc -l < "$SCREEN_LOG") + 1 ))
fi

step "[${TEST_NAME}] Running test inside guest..."
GUEST_TEST_STARTED_AT="$(date -u +'%Y-%m-%dT%H:%M:%S.%9NZ')"
send_guest "if ! mountpoint -q /mnt; then mount -t 9p -o trans=virtio,version=9p2000.L cosim_share /mnt; fi; bash /mnt/${GUEST_SCRIPT}"

last_printed=$((start_line - 1))
result_rc=""
guest_run_start=$(date +%s)
while true; do
    if [[ -f "$SCREEN_LOG" ]]; then
        current_lines=$(wc -l < "$SCREEN_LOG")
        if (( current_lines > last_printed )); then
            sed -n "$((last_printed + 1)),${current_lines}p" "$SCREEN_LOG"
            last_printed=$current_lines
        fi
        if tr -d '\r' < "$SCREEN_LOG" | grep -a -q "^__${TOKEN}__:[0-9][0-9]*$"; then
            result_rc="$(tr -d '\r' < "$SCREEN_LOG" | grep -a "^__${TOKEN}__:[0-9][0-9]*$" | tail -1 | sed 's/.*://')"
            break
        fi
    fi
    if ! session_alive; then
        rm -f "$GUEST_SCRIPT_HOST"
        record_category "$COSIM_CAT_QEMU_EXIT" "true"
        error "[${TEST_NAME}] detached session exited before the test finished. Log tail:\n$(tail -n 80 "$SCREEN_LOG" 2>/dev/null)"
    fi
    now_ts=$(date +%s)
    if (( now_ts - guest_run_start >= GUEST_RUN_TIMEOUT_SECS )); then
        rm -f "$GUEST_SCRIPT_HOST"
        record_category "$COSIM_CAT_TEST_TIMEOUT"
        error "[${TEST_NAME}] compile/test did not finish within ${GUEST_RUN_TIMEOUT_SECS}s"
    fi
    sleep 1
done
if ! session_alive; then
    rm -f "$GUEST_SCRIPT_HOST"
    record_category "$COSIM_CAT_QEMU_EXIT" "true"
    error "[${TEST_NAME}] detached session exited after emitting the test completion token. Log tail:\n$(tail -n 80 "$SCREEN_LOG" 2>/dev/null)"
fi
GUEST_TEST_FINISHED_AT="$(date -u +'%Y-%m-%dT%H:%M:%S.%9NZ')"

rm -f "$GUEST_SCRIPT_HOST"

compile_rc="$(tr -d '\r' < "$SCREEN_LOG" | \
    grep -a "^__${COMPILE_TOKEN}__:[0-9][0-9]*$" | tail -n 1 | sed 's/.*://' || true)"
[[ "$compile_rc" =~ ^[0-9]+$ ]] || {
    record_category "$COSIM_CAT_TEST_FAIL"
    error "[${TEST_NAME}] compile completion token is missing or invalid"
}

TEST_BINARY="${STAGING_DIR}/build/${TEST_NAME}"
if [[ "$compile_rc" -eq 0 && ! -x "$TEST_BINARY" ]]; then
    record_category "$COSIM_CAT_TEST_FAIL"
    error "[${TEST_NAME}] compile succeeded but exact binary is missing: $TEST_BINARY"
fi
if [[ -f "$TEST_BINARY" ]]; then
    {
        echo "test_binary=${TEST_BINARY}"
        echo "test_binary_sha256=$(sha256sum "$TEST_BINARY" | awk '{print $1}')"
    } >> "${PATCH_DIR}/binary-provenance.txt"
fi

normalised_output="$(sed -n "${start_line},\$p" "$SCREEN_LOG" | tr -d '\r')"
pass_count="$(awk -v marker="[PASS] ${TEST_NAME}" '$0 == marker {count++} END {print count + 0}' \
    <<<"$normalised_output")"
fail_count="$(awk '/^\[FAIL\] / {count++} END {print count + 0}' \
    <<<"$normalised_output")"
if [[ "$result_rc" -eq 0 && ( "$pass_count" -ne 1 || "$fail_count" -ne 0 ) ]]; then
    warn "[${TEST_NAME}] invalid result contract: pass_count=${pass_count}, fail_count=${fail_count}"
    result_rc=1
fi

if [[ "$result_rc" -eq 0 ]]; then
    record_category "$COSIM_CAT_TEST_PASS"
else
    record_category "$COSIM_CAT_TEST_FAIL"
fi

cname="$(cosim_container_name "$COSIM_RUN_ID")"
docker inspect "$cname" > "${RUNNER_ARTIFACT_DIR}/docker-inspect.json" 2>&1 || true
{
    echo "run_id=${COSIM_RUN_ID}"
    if [[ "$result_rc" -eq 0 ]]; then
        echo "category=${COSIM_CAT_TEST_PASS}"
    else
        echo "category=${COSIM_CAT_TEST_FAIL}"
    fi
    echo "program=${TEST_NAME}"
    echo "test=${TEST_NAME}"
    echo "program_source=tests/kernels/${TEST_NAME}.cpp"
    echo "program_binary=tests/build/${TEST_NAME}"
    echo "runner_argument=${TEST_NAME}"
    echo "mode=${RUNNER_MODE}"
    echo "repeat_count=1"
    echo "timeout_policy=${RUNNER_TIMEOUT_POLICY}"
    echo "boot_timeout=${BOOT_TIMEOUT_SECS}"
    echo "test_timeout=${TEST_TIMEOUT_SECS}"
    echo "guest_run_timeout=${GUEST_RUN_TIMEOUT_SECS}"
    echo "guest_test_prefix=${RECORDED_GUEST_TEST_PREFIX}"
    echo "guest_test_prefix_input=${GUEST_TEST_PREFIX}"
    echo "expected_hsa_enable_interrupt=${GUEST_HSA_ENABLE_INTERRUPT}"
    echo "gem5_binary=${EFFECTIVE_GEM5_BIN}"
    echo "gem5_docker_image_name=${EFFECTIVE_GEM5_DOCKER_IMAGE}"
    echo "gem5_docker_image=${GEM5_CURRENT_DOCKER_IMAGE_ID}"
    echo "gem5_config_args=${GEM5_CONFIG_ARGS}"
    echo "strict_acceptance=${STRICT_ACCEPTANCE}"
    echo "artifact_dir_pattern=-"
    echo "guest_bridge_policy=${GUEST_BRIDGE_POLICY}"
    echo "guest_bridge_host=${STAGING_DIR}"
    echo "guest_bridge_guest=/mnt"
    echo "compile_exit_code=${compile_rc}"
    echo "test_exit_code=${result_rc}"
    echo "exit_code=${result_rc}"
    echo "pass_count=${pass_count}"
    echo "fail_count=${fail_count}"
    echo "guest_test_started_at=${GUEST_TEST_STARTED_AT}"
    echo "guest_test_finished_at=${GUEST_TEST_FINISHED_AT}"
    echo "source_snapshot=${PATCH_DIR}/source-snapshot.txt"
    echo "gem5_baseline_lock=${GEM5_BASELINE_LOCK_ARCHIVE}"
    echo "gem5_baseline_lock_sha256=${GEM5_BASELINE_LOCK_SHA256}"
    echo "runner_invocation=${RUNNER_ARTIFACT_DIR}/runner-invocation.txt"
    echo "launch_invocation=${RUNNER_ARTIFACT_DIR}/launch-invocation.txt"
    echo "guest_script=${GUEST_SCRIPT_ARCHIVE}"
} > "${RUNNER_ARTIFACT_DIR}/runner-metadata.txt"

cleanup_rc=0
if [[ "$KEEP_ALIVE_ON_SUCCESS" -eq 1 && "$result_rc" -eq 0 ]]; then
    info "[${TEST_NAME}] Leaving QEMU + gem5 running (--keep-alive)"
    info "Console log: ${SCREEN_LOG}"
    info "Console pipe: ${SESSION_FIFO}"
    cleanup_rc=1
    {
        echo "cleanup_status=preserved"
        echo "cleanup_exit_code=${cleanup_rc}"
    } >> "${RUNNER_ARTIFACT_DIR}/runner-metadata.txt"
else
    step "[${TEST_NAME}] Cleaning up detached session..."
    if cleanup_session; then
        {
            echo "cleanup_status=verified"
            echo "cleanup_exit_code=0"
        } >> "${RUNNER_ARTIFACT_DIR}/runner-metadata.txt"
    else
        cleanup_rc=$?
        result_rc=1
        {
            echo "cleanup_status=failed"
            echo "cleanup_exit_code=${cleanup_rc}"
        } >> "${RUNNER_ARTIFACT_DIR}/runner-metadata.txt"
    fi
fi

exec {CONTROL_FD}>&-

[[ -f "$SCREEN_LOG" && ! -L "$SCREEN_LOG" ]] || \
    error "canonical QEMU log is missing or symlinked: ${SCREEN_LOG}"
if ! QEMU_LOG_SHA256="$(python3 -B "${SCRIPT_DIR}/cosim_log_evidence.py" \
        stable-sha256 "$SCREEN_LOG")"; then
    error "canonical QEMU log failed stable snapshot hashing: ${SCREEN_LOG}"
fi
[[ "$QEMU_LOG_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
    error "canonical QEMU log produced an invalid SHA-256: ${SCREEN_LOG}"

GEM5_LOG="${RUNNER_ARTIFACT_DIR}/gem5.log"
[[ -f "$GEM5_LOG" && ! -L "$GEM5_LOG" ]] || \
    error "canonical gem5 log is missing or symlinked: ${GEM5_LOG}"
if ! GEM5_LOG_SHA256="$(python3 -B "${SCRIPT_DIR}/cosim_log_evidence.py" \
        stable-sha256 "$GEM5_LOG")"; then
    error "canonical gem5 log failed stable snapshot hashing: ${GEM5_LOG}"
fi
[[ "$GEM5_LOG_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
    error "canonical gem5 log produced an invalid SHA-256: ${GEM5_LOG}"
{
    echo "qemu_log_sha256=${QEMU_LOG_SHA256}"
    echo "gem5_log_sha256=${GEM5_LOG_SHA256}"
} >> "${RUNNER_ARTIFACT_DIR}/runner-metadata.txt"

classifier_rc=0
if python3 "${SCRIPT_DIR}/classify_runs.py" \
    --artifact-dir "$RUNNER_ARTIFACT_DIR" \
    --program "$TEST_NAME" \
    --write-verdict "${RUNNER_ARTIFACT_DIR}/verdict.json" \
    --json > "${RUNNER_ARTIFACT_DIR}/classifier-output.json"; then
    info "[${TEST_NAME}] Evidence classifier accepted the run"
else
    classifier_rc=$?
    warn "[${TEST_NAME}] Evidence classifier rejected the run; see verdict.json"
    result_rc=1
    record_category "$COSIM_CAT_TEST_FAIL"
fi

if [[ "$cleanup_rc" -ne 0 || "$classifier_rc" -ne 0 ]]; then
    result_rc=1
fi

if [[ -f "${RUNNER_ARTIFACT_DIR}/verdict.json" ]]; then
    verdict_outcome="$(python3 -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["outcome"])' \
        "${RUNNER_ARTIFACT_DIR}/verdict.json")"
    verdict_reason="$(python3 -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["reason"])' \
        "${RUNNER_ARTIFACT_DIR}/verdict.json")"
    hsa_interrupt="$(cosim_guest_hsa_interrupt_from_log "$SCREEN_LOG" || true)"
    {
        printf 'program\thsa_interrupt\trun\tsession_id\toutcome\texit_code\treason\tartifact_dir\tboot_timeout\ttest_timeout\tguest_run_timeout\tstrict_acceptance\n'
        printf '%s\t%s\t1\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$TEST_NAME" "${hsa_interrupt:-unknown}" "$COSIM_RUN_ID" \
            "$verdict_outcome" "$result_rc" "$verdict_reason" "$RUNNER_ARTIFACT_DIR" \
            "$BOOT_TIMEOUT_SECS" "$TEST_TIMEOUT_SECS" "$GUEST_RUN_TIMEOUT_SECS" \
            "$STRICT_ACCEPTANCE"
    } > "${RUNNER_ARTIFACT_DIR}/matrix.tsv"
fi

exit "$result_rc"
