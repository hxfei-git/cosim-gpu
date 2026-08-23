#!/bin/bash
# Host-side single-operator test runner.
# Run one operator per QEMU + gem5 session to avoid cross-test state corruption.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
TESTS_DIR="${COSIM_DIR}/tests"
KERNELS_DIR="${TESTS_DIR}/kernels"
LAUNCH_SCRIPT="${SCRIPT_DIR}/cosim_launch.sh"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/cosim_lib.sh"

SESSION_NAME="${SESSION_NAME:-qemu-cosim-tests}"
SCREEN_LOG="${SCREEN_LOG:-}"
BOOT_TIMEOUT_SECS="${BOOT_TIMEOUT_SECS:-240}"
TEST_TIMEOUT_SECS="${TEST_TIMEOUT_SECS:-60}"
GUEST_RUN_TIMEOUT_SECS="${GUEST_RUN_TIMEOUT_SECS:-1800}"
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

usage() {
    cat <<EOF
Host-side single-operator cosim test runner.

Usage: $0 [options] <operator-filter>

Options:
  --all                  Run all operators, one fresh cosim session each
  --repeat N             Run the same operator N times (fresh session each)
  --keep-alive           Leave QEMU + gem5 running after a successful test
  --session-name NAME    detached session name (default: qemu-cosim-tests)
  --screen-log PATH      console log path (default: /tmp/qemu-cosim-tests.log)
  --boot-timeout SECS    guest boot timeout (default: 240)
  --test-timeout SECS    per-test timeout inside guest (default: 60)
  --guest-run-timeout S  host deadline for compile + test (default: 1800)
  --output-dir DIR        artifact directory below repository artifacts/
  -h, --help             Show this help

Unknown options are passed through to cosim_launch.sh.
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
        --*)                PASSTHROUGH_ARGS+=("$1" "$2"); shift 2 ;;
        *)                  FILTER="$1"; shift ;;
    esac
done

for timeout_name in BOOT_TIMEOUT_SECS TEST_TIMEOUT_SECS GUEST_RUN_TIMEOUT_SECS; do
    timeout_value="${!timeout_name}"
    [[ "$timeout_value" =~ ^[1-9][0-9]*$ ]] || \
        error "${timeout_name} must be a positive integer"
done
[[ "$REPEAT_COUNT" =~ ^[0-9]+$ ]] || error "--repeat must be a non-negative integer"

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
        warn "Cleanup: ${SCRIPT_DIR}/cosim_cleanup.sh --run-id ${COSIM_RUN_ID:-unknown} --confirm"
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
TOKEN="COSIM_TEST_DONE_${TEST_NAME}_$(date +%s)"
COMPILE_TOKEN="COSIM_COMPILE_DONE_${TEST_NAME}_$(date +%s)"

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

if [[ "$SCREEN_LOG_SET" -eq 0 ]]; then
    SCREEN_LOG="${RUNNER_ARTIFACT_DIR}/qemu.log"
else
    SCREEN_LOG="$(realpath -m -- "$SCREEN_LOG")"
    case "$SCREEN_LOG" in
        "${RUNNER_ARTIFACT_DIR}/"*) ;;
        *) error "--screen-log must be inside --output-dir" ;;
    esac
fi

STAGING_DIR="${RUNNER_ARTIFACT_DIR}/staging"
[[ ! -e "$STAGING_DIR" && ! -L "$STAGING_DIR" ]] || error "staging path already exists"
mkdir -p "$STAGING_DIR"
rsync -a --exclude build/ --exclude '.cosim_guest_run.*' \
    "${TESTS_DIR}/" "${STAGING_DIR}/"
GUEST_SCRIPT_HOST="${STAGING_DIR}/${GUEST_SCRIPT}"

PATCH_DIR="${RUNNER_ARTIFACT_DIR}/patch"
mkdir -p "$PATCH_DIR"
SOURCE_FINGERPRINT="$(
    cd "$STAGING_DIR"
    find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
)"
{
    echo "head_commit=$(git -C "$COSIM_DIR" rev-parse HEAD)"
    echo "source_fingerprint=${SOURCE_FINGERPRINT}"
    echo "program=${TEST_NAME}"
} > "${PATCH_DIR}/source-snapshot.txt"

git -C "${COSIM_DIR}/gem5" status --short > "${PATCH_DIR}/gem5-status.txt"
git -C "${COSIM_DIR}/gem5" diff --binary HEAD > "${PATCH_DIR}/gem5.patch"
git -C "${COSIM_DIR}/gem5" ls-files --others --exclude-standard > \
    "${PATCH_DIR}/untracked-files.txt"
if [[ -s "${PATCH_DIR}/untracked-files.txt" ]]; then
    git -C "${COSIM_DIR}/gem5" ls-files -z --others --exclude-standard | \
        tar -C "${COSIM_DIR}/gem5" --null --files-from=- -cf \
            "${PATCH_DIR}/untracked-files.tar"
fi

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

cat >"$GUEST_SCRIPT_HOST" <<EOF
#!/bin/bash
set -uo pipefail

export HSA_ENABLE_INTERRUPT="\${HSA_ENABLE_INTERRUPT:-0}"
case "\$HSA_ENABLE_INTERRUPT" in
    0|1) ;;
    *) echo "invalid HSA_ENABLE_INTERRUPT=\$HSA_ENABLE_INTERRUPT"; exit 2 ;;
esac
echo "[COSIM_ENV] HSA_ENABLE_INTERRUPT=\$HSA_ENABLE_INTERRUPT"

if ! mountpoint -q /mnt; then
    mount -t 9p -o trans=virtio,version=9p2000.L cosim_share /mnt
fi

cd /mnt || exit 2
make -j1
build_rc=\$?
echo "__${COMPILE_TOKEN}__:\${build_rc}"
if [[ "\$build_rc" -ne 0 ]]; then
    echo "__${TOKEN}__:\${build_rc}"
    exit "\$build_rc"
fi
TEST_TIMEOUT_SECS=${TEST_TIMEOUT_SECS} ./run_tests.sh ${TEST_NAME}
rc=\$?
echo "__${TOKEN}__:\${rc}"
exit "\${rc}"
EOF
chmod +x "$GUEST_SCRIPT_HOST"

step "[${TEST_NAME}] Running test inside guest..."
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
{
    echo "gem5_source_commit=$(git -C "${COSIM_DIR}/gem5" rev-parse HEAD)"
    echo "gem5_binary=${COSIM_DIR}/gem5/build/VEGA_X86/gem5.opt"
    echo "gem5_sha256=$(sha256sum "${COSIM_DIR}/gem5/build/VEGA_X86/gem5.opt" | awk '{print $1}')"
    if [[ -f "$TEST_BINARY" ]]; then
        echo "test_binary=${TEST_BINARY}"
        echo "test_binary_sha256=$(sha256sum "$TEST_BINARY" | awk '{print $1}')"
    fi
} > "${PATCH_DIR}/binary-provenance.txt"

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
    echo "compile_exit_code=${compile_rc}"
    echo "test_exit_code=${result_rc}"
    echo "exit_code=${result_rc}"
    echo "pass_count=${pass_count}"
    echo "fail_count=${fail_count}"
    echo "source_snapshot=${PATCH_DIR}/source-snapshot.txt"
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
    hsa_interrupt="$(awk -F= '/^\[COSIM_ENV\] HSA_ENABLE_INTERRUPT=[01]$/ {print $2; exit}' \
        <<<"$normalised_output")"
    {
        printf 'program\thsa_interrupt\trun\tsession_id\toutcome\texit_code\treason\tartifact_dir\n'
        printf '%s\t%s\t1\t%s\t%s\t%s\t%s\t%s\n' \
            "$TEST_NAME" "${hsa_interrupt:-unknown}" "$COSIM_RUN_ID" \
            "$verdict_outcome" "$result_rc" "$verdict_reason" "$RUNNER_ARTIFACT_DIR"
    } > "${RUNNER_ARTIFACT_DIR}/matrix.tsv"
fi

exit "$result_rc"
