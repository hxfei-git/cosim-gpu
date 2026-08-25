#!/bin/bash
# ==========================================================================
# QEMU + gem5 MI300X Co-simulation Launcher
#
# gem5 runs inside Docker (GPU-only, no kernel), while the pinned local QEMU
# runs on the host with KVM and connects through standard vfio-user.
#
# Usage:
#   ./scripts/cosim_launch.sh                              # vfio-user
#   ./scripts/cosim_launch.sh --gem5-debug MI300XCosim      # with debug
#   ./scripts/cosim_launch.sh --help
# ==========================================================================

set -euo pipefail

ORIGINAL_ARGS=("$@")

# ---- Shared library ----

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/cosim_lib.sh"

# ---- Run-ID ----

COSIM_RUN_ID="${COSIM_RUN_ID:-$(generate_run_id)}"
export COSIM_RUN_ID

# ---- Path defaults ----
GEM5_DIR="${COSIM_DIR}/gem5"
RESOURCES_DIR="${COSIM_DIR}/gem5-resources"

CANONICAL_GEM5_BIN="${GEM5_DIR}/build/VEGA_X86/gem5.opt"
GEM5_BIN="$CANONICAL_GEM5_BIN"
# shellcheck disable=SC2034
GEM5_CONFIG="${GEM5_DIR}/configs/example/gpufs/mi300_cosim.py"
GEM5_DOCKER_IMAGE="${GEM5_DOCKER_IMAGE:-gem5-run:local}"
GEM5_CONTAINER="$(cosim_container_name "$COSIM_RUN_ID")"

LOCAL_QEMU_BIN="${COSIM_DIR}/.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64"
LOCAL_QEMU_IMG="${COSIM_DIR}/.local/cosim/qemu/10.1.5/bin/qemu-img"
QEMU_BIN="${QEMU_BIN:-}"
QEMU_IMG="${QEMU_IMG:-}"
if [[ -z "$QEMU_BIN" && -x "$LOCAL_QEMU_BIN" ]]; then
    QEMU_BIN="$LOCAL_QEMU_BIN"
elif [[ -z "$QEMU_BIN" ]]; then
    QEMU_BIN="$(command -v qemu-system-x86_64 2>/dev/null || true)"
fi
if [[ -z "$QEMU_IMG" && -x "$LOCAL_QEMU_IMG" ]]; then
    QEMU_IMG="$LOCAL_QEMU_IMG"
elif [[ -z "$QEMU_IMG" && -n "$QEMU_BIN" && \
        -x "$(dirname "$QEMU_BIN")/qemu-img" ]]; then
    QEMU_IMG="$(dirname "$QEMU_BIN")/qemu-img"
elif [[ -z "$QEMU_IMG" ]]; then
    QEMU_IMG="$(command -v qemu-img 2>/dev/null || true)"
fi
DISK_IMAGE="${RESOURCES_DIR}/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70"
KERNEL="${RESOURCES_DIR}/src/x86-ubuntu-gpu-ml/vmlinux-rocm70"

SOCKET_PATH="/tmp/gem5-mi300x-${COSIM_RUN_ID}.sock"
SHMEM_PATH="/mi300x-vram-${COSIM_RUN_ID}"
SHMEM_HOST_PATH="/cosim-guest-ram-${COSIM_RUN_ID}"

HOST_MEM="8G"
HOST_CPUS="4"
VRAM_SIZE="16GiB"
NUM_CUS="40"
GEM5_DEBUG=""
GEM5_TIMEOUT=120
QEMU_TRACE=""
SHARE_DIR=""
NUM_GPUS="1"
FORCE_CLEAN=""
FORCE_CLEAN_CONFIRM=""

SESSION_DIR="/tmp/cosim-${COSIM_RUN_ID}.session"
GUEST_OVERLAY="${SESSION_DIR}/guest-overlay.qcow2"
SCREEN_LOG="/tmp/cosim-${COSIM_RUN_ID}.log"
ARTIFACT_DIR="${COSIM_DIR}/artifacts/standalone/${COSIM_RUN_ID}"
COSIM_FAILURE_CATEGORY=""
COSIM_SECONDARY_STATUS=""
GEM5_LOG_PID=""

# ---- Colors ----

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
# Called from EXIT-trap paths that static analysis cannot always follow.
# shellcheck disable=SC2317
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "${CYAN}[STEP]${NC} $*"; }

# ---- Argument parsing ----

usage() {
    cat <<EOF
QEMU + gem5 MI300X Co-simulation Launcher

Usage: $0 [options]

Options:
  --disk-image PATH       Disk image  (default: auto-detect in gem5-resources)
  --kernel PATH           vmlinux     (default: auto-detect in gem5-resources)
  --qemu-bin PATH         QEMU binary (default: pinned repository-local QEMU)
  --gem5-bin PATH         gem5 binary (default: build/VEGA_X86/gem5.opt)
  --gem5-docker IMAGE     Docker image for gem5 (default: gem5-run:local)
  --socket-path PATH      Unix socket（默认：/tmp/gem5-mi300x-<run-id>.sock）
  --host-mem SIZE         Guest RAM   (default: 8G)
  --host-cpus N           Guest CPUs  (default: 4)
  --vram-size SIZE        GPU VRAM    (default: 16GiB)
  --num-cus N             Compute units (default: 40)
  --gem5-debug FLAGS      gem5 debug flags (e.g. MI300XCosim,AMDGPUDevice)
  --qemu-trace EVENTS     QEMU trace events
  --share-dir PATH        Share host dir with guest via 9p (mount tag: cosim_share)
  --num-gpus N            Number of GPU instances (default: 1)
  --timeout SECS          gem5 init timeout (default: 120)
  --artifact-dir PATH     Run artifact directory under this repository
  --force-clean           List orphaned cosim resources (dry-run)
  --confirm               Reserved; unscoped deletion is refused
  -h, --help              Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --disk-image)    DISK_IMAGE="$2";       shift 2 ;;
        --kernel)        KERNEL="$2";           shift 2 ;;
        --qemu-bin)      QEMU_BIN="$2";         shift 2 ;;
        --gem5-bin)      GEM5_BIN="$2";         shift 2 ;;
        --gem5-docker)   GEM5_DOCKER_IMAGE="$2"; shift 2 ;;
        --socket-path)   SOCKET_PATH="$2";      shift 2 ;;
        --host-mem)      HOST_MEM="$2";         shift 2 ;;
        --host-cpus)     HOST_CPUS="$2";        shift 2 ;;
        --vram-size)     VRAM_SIZE="$2";        shift 2 ;;
        --num-cus)       NUM_CUS="$2";          shift 2 ;;
        --gem5-debug)    GEM5_DEBUG="$2";       shift 2 ;;
        --qemu-trace)    QEMU_TRACE="$2";       shift 2 ;;
        --share-dir)     SHARE_DIR="$2";        shift 2 ;;
        --num-gpus)      NUM_GPUS="$2";         shift 2 ;;
        --timeout)       GEM5_TIMEOUT="$2";     shift 2 ;;
        --artifact-dir)  ARTIFACT_DIR="$2";     shift 2 ;;
        --force-clean)   FORCE_CLEAN=1;         shift ;;
        --confirm)       FORCE_CLEAN_CONFIRM=1; shift ;;
        -h|--help)       usage ;;
        *)               echo "Unknown option: $1"; usage ;;
    esac
done

[[ "$COSIM_RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
    error "unsafe COSIM_RUN_ID: $COSIM_RUN_ID"
[[ "$COSIM_RUN_ID" != *..* ]] || error "unsafe COSIM_RUN_ID: $COSIM_RUN_ID"
for numeric_name in HOST_CPUS NUM_CUS NUM_GPUS GEM5_TIMEOUT; do
    numeric_value="${!numeric_name}"
    [[ "$numeric_value" =~ ^[1-9][0-9]*$ ]] || error "${numeric_name} must be a positive integer"
done
for invocation_value in "$GEM5_BIN" "$GEM5_DEBUG" "$HOST_MEM" "$VRAM_SIZE"; do
    [[ "$invocation_value" != *$'\n'* && "$invocation_value" != *$'\r'* && \
       "$invocation_value" != *$'\t'* ]] || \
        error "control whitespace is not allowed in invocation values"
done
ARTIFACT_DIR="$(realpath -m -- "$ARTIFACT_DIR")"
case "$ARTIFACT_DIR" in
    "${COSIM_DIR}/artifacts/"*) ;;
    *) error "--artifact-dir must be below ${COSIM_DIR}/artifacts" ;;
esac

# Handle --force-clean mode
if [[ -n "$FORCE_CLEAN" ]]; then
    info "Force-clean mode (run-ID: $COSIM_RUN_ID)"
    if [[ -n "$FORCE_CLEAN_CONFIRM" ]]; then
        error "Unscoped deletion is refused; use cosim_cleanup.sh with a valid manifest."
    else
        info "Dry-run: listing orphaned cosim resources..."
        force_clean_orphans "false"
    fi
    exit 0
fi

# ---- Multi-GPU validation ----

if [[ "$NUM_GPUS" -lt 1 ]]; then
    error "--num-gpus must be >= 1"
fi
# ---- Derived paths ----

SHMEM_HOST_FILE="/dev/shm${SHMEM_HOST_PATH}"

# Per-GPU socket and VRAM shmem paths
# 单 GPU 运行同样使用带 run ID 的 socket、Guest RAM 与 VRAM 资源名。
# 多 GPU：/tmp/gem5-mi300x-<run-id>-{0..N-1}.sock 与
# /dev/shm/mi300x-vram-<run-id>-{0..N-1}。
gpu_socket_path() {
    local gpu_id=$1
    if [[ "$NUM_GPUS" -eq 1 ]]; then
        echo "$SOCKET_PATH"
    else
        local stem="${SOCKET_PATH%.sock}"
        echo "${stem}-${gpu_id}.sock"
    fi
}

gpu_shmem_file() {
    local gpu_id=$1
    if [[ "$NUM_GPUS" -eq 1 ]]; then
        echo "/dev/shm${SHMEM_PATH}"
    else
        echo "/dev/shm${SHMEM_PATH}-${gpu_id}"
    fi
}

# Container paths (gem5 source mounted at /gem5)
[[ -x "$CANONICAL_GEM5_BIN" && ! -L "$CANONICAL_GEM5_BIN" ]] || \
    error "canonical gem5 binary is missing, non-executable, or symlinked: ${CANONICAL_GEM5_BIN}"
CANONICAL_GEM5_REALPATH="$(realpath -e -- "$CANONICAL_GEM5_BIN")"
[[ "$CANONICAL_GEM5_REALPATH" == "$CANONICAL_GEM5_BIN" ]] || \
    error "canonical gem5 binary resolves outside its fixed path: ${CANONICAL_GEM5_BIN}"
REQUESTED_GEM5_BIN="$GEM5_BIN"
if ! GEM5_BIN="$(realpath -e -- "$REQUESTED_GEM5_BIN")"; then
    error "gem5 not found: $REQUESTED_GEM5_BIN"
fi
[[ "$GEM5_BIN" == "$CANONICAL_GEM5_REALPATH" ]] || \
    error "--gem5-bin must resolve to ${CANONICAL_GEM5_BIN}"
C_GEM5_BIN="/gem5/build/VEGA_X86/gem5.opt"
C_GEM5_CONFIG="/gem5/configs/example/gpufs/mi300_cosim.py"
GEM5_CONFIG_ARGS="defaults:num-gpus=${NUM_GPUS},num-cus=${NUM_CUS},host-mem=${HOST_MEM},vram-size=${VRAM_SIZE}"
if [[ -n "$GEM5_DEBUG" ]]; then
    GEM5_CONFIG_ARGS+=";debug-flags=${GEM5_DEBUG}"
fi

# ---- Validation ----

[[ -x "$GEM5_BIN" ]]   || error "gem5 not executable: $GEM5_BIN\n  Build: ./scripts/cosim_build.sh gem5"
[[ -n "$QEMU_BIN" && -x "$QEMU_BIN" ]] || error "qemu-system-x86_64 not found. Install QEMU 10.1+ or pass --qemu-bin."
[[ -n "$QEMU_IMG" && -x "$QEMU_IMG" ]] || \
    error "qemu-img not found beside the selected QEMU or on PATH."
"$QEMU_BIN" -device help 2>/dev/null | grep 'vfio-user-pci' >/dev/null || \
    error "QEMU does not provide vfio-user-pci. Install QEMU 10.1+ or pass a compatible --qemu-bin."
[[ -f "$DISK_IMAGE" ]] || error "Disk image not found: $DISK_IMAGE\n  Build: ./scripts/cosim_build.sh guest"
[[ -f "$KERNEL" ]]     || error "Kernel not found: $KERNEL\n  Build: ./scripts/cosim_build.sh guest"
[[ -r /dev/kvm && -w /dev/kvm ]] || error "/dev/kvm must be readable and writable."
[[ "$SOCKET_PATH" == "/tmp/gem5-mi300x-${COSIM_RUN_ID}.sock" ]] || \
    error "--socket-path must remain run-scoped: /tmp/gem5-mi300x-${COSIM_RUN_ID}.sock"
if [[ -n "$SHARE_DIR" ]]; then
    SHARE_DIR="$(realpath -e -- "$SHARE_DIR")"
    [[ -d "$SHARE_DIR" ]] || error "--share-dir is not a directory: $SHARE_DIR"
fi

docker info >/dev/null 2>&1 || error "Docker not running"
docker image inspect "$GEM5_DOCKER_IMAGE" >/dev/null 2>&1 || \
    error "Docker image '$GEM5_DOCKER_IMAGE' not found.\n  Build: ./scripts/cosim_build.sh gem5"

# ---- Session and manifest setup ----

mkdir -p "${COSIM_DIR}/.local/cosim"
exec 8>"${COSIM_DIR}/.local/cosim/runtime.lock"
flock -n 8 || error "another cosim session owns ${COSIM_DIR}/.local/cosim/runtime.lock"

[[ ! -e "$SESSION_DIR" && ! -L "$SESSION_DIR" ]] || \
    error "stale or symlinked session directory exists: $SESSION_DIR"
manifest_init "$SESSION_DIR" "$COSIM_RUN_ID" "$COSIM_DIR"

manifest_add "runtime" "container" "$GEM5_CONTAINER"
manifest_add "runtime" "shmem" "$SHMEM_HOST_FILE"
for ((g=0; g<NUM_GPUS; g++)); do
    manifest_add "runtime" "shmem" "$(gpu_shmem_file "$g")"
    manifest_add "runtime" "socket" "$(gpu_socket_path "$g")"
done
manifest_add "runtime" "file" "$GUEST_OVERLAY"
manifest_add "runtime" "directory" "$SESSION_DIR"
manifest_add "artifact" "directory" "$ARTIFACT_DIR"

# ---- Cleanup handler ----

# shellcheck disable=SC2317
cleanup() {
    local exit_code="${1:-$?}"
    local cleanup_result="PASS"
    echo ""

    if [[ -z "$COSIM_FAILURE_CATEGORY" ]]; then
        if [[ "$exit_code" -eq 0 ]]; then
            COSIM_FAILURE_CATEGORY="$COSIM_CAT_LAUNCHER_EXIT"
        else
            COSIM_FAILURE_CATEGORY="$COSIM_CAT_INFRA_UNKNOWN"
        fi
    fi

    # Persist infrastructure status with the immutable run evidence.
    mkdir -p "$ARTIFACT_DIR"
    printf '%s\n' "$COSIM_FAILURE_CATEGORY" > \
        "${ARTIFACT_DIR}/launcher-category.txt" 2>/dev/null || true

    if [[ "$COSIM_FAILURE_CATEGORY" != "$COSIM_CAT_TEST_PASS" ]]; then
        info "Capturing diagnostic artifacts (category: $COSIM_FAILURE_CATEGORY)..."
        capture_artifacts "$ARTIFACT_DIR" "$GEM5_CONTAINER" "$SCREEN_LOG" \
            "$COSIM_RUN_ID" "$COSIM_FAILURE_CATEGORY"
    fi

    info "Shutting down co-simulation (run-ID: $COSIM_RUN_ID)..."
    if ! cleanup_from_manifest "$GEM5_CONTAINER"; then
        COSIM_SECONDARY_STATUS="$COSIM_CAT_CLEANUP_FAIL"
        cleanup_result="FAIL"
        warn "Cleanup manifest contained an unsafe or failed entry."
    fi
    if [[ -n "$GEM5_LOG_PID" ]]; then
        wait "$GEM5_LOG_PID" 2>/dev/null || true
    fi

    if verify_cleanup 10 "$GEM5_CONTAINER"; then
        info "Teardown verified."
    else
        COSIM_SECONDARY_STATUS="$COSIM_CAT_CLEANUP_FAIL"
        cleanup_result="FAIL"
        warn "Teardown verification failed: some resources may remain."
    fi

    {
        echo "result=${cleanup_result}"
        echo "primary_category=${COSIM_FAILURE_CATEGORY}"
        echo "secondary_category=${COSIM_SECONDARY_STATUS:-none}"
    } > "${ARTIFACT_DIR}/cleanup-status.txt"

    info "Run: $COSIM_RUN_ID | Category: $COSIM_FAILURE_CATEGORY${COSIM_SECONDARY_STATUS:+ | Secondary: $COSIM_SECONDARY_STATUS}"
}
trap 'cleanup' EXIT
trap 'COSIM_FAILURE_CATEGORY="$COSIM_CAT_INTERRUPT"; exit 130' INT TERM

# ---- Preflight audit ----

mkdir -p "$ARTIFACT_DIR"
{
    echo "schema=cosim-launch-invocation/v1"
    echo "run_id=${COSIM_RUN_ID}"
    echo "artifact_dir=${ARTIFACT_DIR}"
    echo "share_dir=${SHARE_DIR}"
    echo "gem5_binary=${GEM5_BIN}"
    echo "gem5_container_binary=${C_GEM5_BIN}"
    echo "gem5_config_args=${GEM5_CONFIG_ARGS}"
    echo "gem5_docker_image=${GEM5_DOCKER_IMAGE}"
    echo "qemu_binary=${QEMU_BIN}"
    echo "disk_image=${DISK_IMAGE}"
    echo "kernel=${KERNEL}"
    echo "host_cpus=${HOST_CPUS}"
    echo "gem5_init_timeout=${GEM5_TIMEOUT}"
    echo "cwd=$(pwd -P)"
    printf 'argv0=%q\n' "$0"
    printf 'argv='
    printf ' %q' "${ORIGINAL_ARGS[@]}"
    printf '\n'
} > "${ARTIFACT_DIR}/launch-invocation.txt"

PREFLIGHT_DIR="${ARTIFACT_DIR}/preflight"
mkdir -p "$PREFLIGHT_DIR"
if ! QEMU_BIN="$QEMU_BIN" GEM5_DOCKER_IMAGE="$GEM5_DOCKER_IMAGE" \
    "${SCRIPT_DIR}/cosim_preflight.sh" run --output-dir "$PREFLIGHT_DIR" | \
    tee "${PREFLIGHT_DIR}/preflight.log"; then
    COSIM_FAILURE_CATEGORY="$COSIM_CAT_READINESS_FAIL"
    error "run preflight failed; inspect ${PREFLIGHT_DIR}/preflight.json"
fi

"$QEMU_IMG" create -q -f qcow2 -F raw -b "$DISK_IMAGE" "$GUEST_OVERLAY" || \
    error "failed to create run-scoped Guest overlay: $GUEST_OVERLAY"
"$QEMU_IMG" info --output=json "$GUEST_OVERLAY" > \
    "${ARTIFACT_DIR}/guest-overlay.json"
{
    echo "path=${DISK_IMAGE}"
    stat -c 'size=%s' "$DISK_IMAGE"
    stat -c 'mtime=%y' "$DISK_IMAGE"
} > "${ARTIFACT_DIR}/guest-base-stat.txt"
if [[ -f "${COSIM_DIR}/.local/cosim/build/guest/.cosim-build-meta" ]]; then
    cp "${COSIM_DIR}/.local/cosim/build/guest/.cosim-build-meta" \
        "${ARTIFACT_DIR}/guest-build-meta.txt"
fi

if ! run_preflight_audit | \
    tee "${SESSION_DIR}/preflight-resources.log" \
        "${ARTIFACT_DIR}/preflight-resources.log"; then
    COSIM_FAILURE_CATEGORY="$COSIM_CAT_READINESS_FAIL"
    error "run-scoped resource preflight failed"
fi

# ==================================================================
# Step 1: Start gem5 in Docker
# ==================================================================

step "Starting gem5 MI300X GPU model in Docker..."

GEM5_DOCKER_CMD=(
    docker run -d
    --name "$GEM5_CONTAINER"
    --label "io.cosim-gpu.run-id=${COSIM_RUN_ID}"
    --label "io.cosim-gpu.repo-root=${COSIM_DIR}"
    --user "$(id -u):$(id -g)"
    -v "${GEM5_DIR}:/gem5"
    -v /tmp:/tmp
    -v /dev/shm:/dev/shm
    -w /gem5
    -e "PYTHONPATH=/usr/lib/python3.12/lib-dynload"
    "$GEM5_DOCKER_IMAGE"
    "$C_GEM5_BIN"
)

if [[ -n "$GEM5_DEBUG" ]]; then
    GEM5_DOCKER_CMD+=("--debug-flags=$GEM5_DEBUG")
fi

GEM5_DOCKER_CMD+=(
    --listener-mode=on
    "$C_GEM5_CONFIG"
    "--socket-path=$SOCKET_PATH"
    "--shmem-path=$SHMEM_PATH"
    "--shmem-host-path=$SHMEM_HOST_PATH"
    "--dgpu-mem-size=$VRAM_SIZE"
    "--num-compute-units=$NUM_CUS"
    "--mem-size=$HOST_MEM"
    "--num-gpus=$NUM_GPUS"
)

"${GEM5_DOCKER_CMD[@]}" >/dev/null

info "gem5 container '$GEM5_CONTAINER' started"
mkdir -p "$ARTIFACT_DIR"
docker logs --follow --timestamps "$GEM5_CONTAINER" \
    > "${ARTIFACT_DIR}/gem5.log" 2>&1 &
GEM5_LOG_PID=$!

# ==================================================================
# Step 2: Wait for gem5 cosim socket to be ready
# ==================================================================

step "Waiting for gem5 to initialize (timeout: ${GEM5_TIMEOUT}s)..."

ELAPSED=0
READY_PATTERN="MI300XVfioUser: listening"

# For multi-GPU, wait until all N bridges report "listening"
EXPECTED_READY_COUNT="$NUM_GPUS"

while true; do
    READY_COUNT=$(docker logs "$GEM5_CONTAINER" 2>&1 | grep -c "$READY_PATTERN" || true)
    if [[ "$READY_COUNT" -ge "$EXPECTED_READY_COUNT" ]]; then
        info "gem5 cosim ready: $READY_COUNT/$EXPECTED_READY_COUNT GPU(s) (${ELAPSED}s, backend=vfio-user)"
        break
    fi

    # Check container still running
    if [[ "$(docker inspect -f '{{.State.Running}}' "$GEM5_CONTAINER" 2>/dev/null)" != "true" ]]; then
        echo ""
        COSIM_FAILURE_CATEGORY="$COSIM_CAT_GEM5_EXIT"
        error "gem5 container exited unexpectedly. Logs:\n$(docker logs "$GEM5_CONTAINER" 2>&1 | tail -20)"
    fi

    sleep 2
    ELAPSED=$((ELAPSED + 2))
    if [[ $ELAPSED -ge $GEM5_TIMEOUT ]]; then
        COSIM_FAILURE_CATEGORY="$COSIM_CAT_GEM5_INIT_TIMEOUT"
        error "gem5 did not become ready in ${GEM5_TIMEOUT}s.\n  Logs: docker logs $GEM5_CONTAINER"
    fi

    # Progress indicator
    if (( ELAPSED % 10 == 0 )); then
        echo -n "."
    fi
done

# ==================================================================
# Step 2.5: Pre-launch health check
# ==================================================================

step "Running pre-launch health check..."

parse_size_bytes() {
    local val="$1"
    local num="${val%%[GgMmKkTt]*}"
    local suffix="${val##*[0-9]}"
    case "${suffix,,}" in
        gib|g)  echo $((num * 1024 * 1024 * 1024)) ;;
        gb)     echo $((num * 1000 * 1000 * 1000)) ;;
        mib|m)  echo $((num * 1024 * 1024)) ;;
        mb)     echo $((num * 1000 * 1000)) ;;
        kib|k)  echo $((num * 1024)) ;;
        kb)     echo $((num * 1000)) ;;
        tib|t)  echo $((num * 1024 * 1024 * 1024 * 1024)) ;;
        tb)     echo $((num * 1000 * 1000 * 1000 * 1000)) ;;
        *)      echo "$num" ;;
    esac
}

EXPECTED_VRAM_BYTES="$(parse_size_bytes "$VRAM_SIZE")"
EXPECTED_RAM_BYTES="$(parse_size_bytes "$HOST_MEM")"

HEALTH_MSG=""
for ((g=0; g<NUM_GPUS; g++)); do
    if HEALTH_MSG="$(check_readiness \
            "$(gpu_socket_path "$g")" \
            "$(gpu_shmem_file "$g")" \
            "$SHMEM_HOST_FILE" \
            "$GEM5_CONTAINER" \
            "$EXPECTED_VRAM_BYTES" \
            "$EXPECTED_RAM_BYTES")"; then
        info "Health check GPU $g passed."
    else
        COSIM_FAILURE_CATEGORY="$COSIM_CAT_READINESS_FAIL"
        error "Pre-launch health check failed (GPU $g): $HEALTH_MSG"
    fi
done

# ==================================================================
# Step 3: Start QEMU
# ==================================================================

KCMDLINE="console=ttyS0,115200 root=/dev/vda1 drm_kms_helper.fbdev_emulation=0 modprobe.blacklist=amdgpu earlyprintk=serial,ttyS0,115200"
step "Starting QEMU (Q35 + KVM, backend=vfio-user)..."

echo "============================================================"
echo "  Run-ID:     $COSIM_RUN_ID"
echo "  Machine:    Q35 + KVM"
echo "  Backend:    vfio-user"
echo "  Num GPUs:   $NUM_GPUS"
echo "  CPUs:       $HOST_CPUS"
echo "  Memory:     $HOST_MEM"
echo "  Disk base:  $(basename "$DISK_IMAGE") (read-only backing)"
echo "  Disk layer: $GUEST_OVERLAY"
echo "  Kernel:     $(basename "$KERNEL")"
for ((g=0; g<NUM_GPUS; g++)); do
    echo "  GPU $g:"
    echo "    Socket:   $(gpu_socket_path "$g")"
    echo "    VRAM SHM: $(gpu_shmem_file "$g")"
done
echo "============================================================"
echo ""
echo "GPU driver loads automatically via cosim-gpu-setup.service (~40s)."
echo "After guest boots (auto-login as root), verify:"
echo "  rocm-smi          # should show device 0x74a0"
echo "  rocminfo          # should show gfx942"
echo ""
echo "若服务缺失或失败，请保留 artifact 与 run ID，并检查 Guest build provenance 和 service log。"
echo "不要手工写 /dev/mem，也不要在部分初始化的 Guest 中重载 amdgpu；清理后使用全新会话。"
echo ""
if [[ -n "$SHARE_DIR" ]]; then
    echo "Shared directory: $SHARE_DIR"
    echo "  In guest, run:"
    echo "  mount -t 9p -o trans=virtio,version=9p2000.L cosim_share /mnt"
    echo ""
fi
echo "Press Ctrl-A X to quit QEMU."
echo "============================================================"
echo ""

# Build QEMU command
QEMU_CMD=(
    "$QEMU_BIN"
    -machine q35
    -enable-kvm -cpu host
    -m "$HOST_MEM"
    -smp "$HOST_CPUS"
    -object "memory-backend-file,id=mem0,size=${HOST_MEM},mem-path=${SHMEM_HOST_FILE},share=on"
    -numa "node,memdev=mem0"
    -kernel "$KERNEL"
    -append "$KCMDLINE"
    -drive "file=$GUEST_OVERLAY,format=qcow2,if=virtio"
    -netdev "user,id=net0,hostfwd=tcp::2222-:22"
    -device "virtio-net-pci,netdev=net0"
)

# Standard vfio-user protocol: one vfio-user-pci device per GPU.
for ((g=0; g<NUM_GPUS; g++)); do
    local_sock="$(gpu_socket_path "$g")"
    QEMU_CMD+=(-device "{\"driver\":\"vfio-user-pci\",\"socket\":{\"type\":\"unix\",\"path\":\"$local_sock\"}}")
done

QEMU_CMD+=(
    -nographic
    -no-reboot
)

if [[ -n "$QEMU_TRACE" ]]; then
    QEMU_CMD+=(-trace "$QEMU_TRACE")
    info "QEMU trace: $QEMU_TRACE"
fi

if [[ -n "$SHARE_DIR" ]]; then
    QEMU_CMD+=(
        -fsdev "local,id=cosim_fs,path=${SHARE_DIR},security_model=none"
        -device "virtio-9p-pci,fsdev=cosim_fs,mount_tag=cosim_share"
    )
    info "Sharing host dir: $SHARE_DIR (mount: mount -t 9p -o trans=virtio cosim_share /mnt)"
fi

# Run QEMU in foreground — do NOT exec, so the EXIT trap runs on QEMU exit
if "${QEMU_CMD[@]}"; then
    QEMU_RC=0
else
    QEMU_RC=$?
    COSIM_FAILURE_CATEGORY="${COSIM_FAILURE_CATEGORY:-$COSIM_CAT_QEMU_EXIT}"
fi
exit $QEMU_RC
