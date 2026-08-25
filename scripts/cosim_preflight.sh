#!/bin/bash
# Strict, read-only preflight gate for the QEMU + gem5 MI300X workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
ARTIFACT_ROOT="${COSIM_DIR}/artifacts"
TOOLCHAIN_LOCK="${COSIM_DIR}/configs/cosim/toolchain.lock"
GUEST_LOCK="${COSIM_DIR}/configs/cosim/guest.lock"
GUEST_PATCH="${SCRIPT_DIR}/patches/0002-guest-core-reproducible.patch"
GUEST_META="${COSIM_DIR}/.local/cosim/build/guest/.cosim-build-meta"
GUEST_SEAL="${COSIM_DIR}/.local/cosim/build/guest/.cosim-content-seal"
GUEST_PROVENANCE_VALIDATOR="${SCRIPT_DIR}/guest_provenance.py"
GEM5_BASELINE_LOCK="${COSIM_DIR}/configs/cosim/gem5-baseline.lock"
QEMU_VERSION="10.1.5"
QEMU_SOURCE_URL="https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz"
QEMU_SIGNATURE_URL="${QEMU_SOURCE_URL}.sig"
QEMU_RELEASE_KEY_FINGERPRINT="CEACC9E15534EBABB82D3FA03353C9CEF108B584"
QEMU_RELEASE_KEY_URL="https://keys.openpgp.org/vks/v1/by-fingerprint/${QEMU_RELEASE_KEY_FINGERPRINT}"
QEMU_SOURCE_DIR="${COSIM_DIR}/.local/cosim/src/qemu-${QEMU_VERSION}"
QEMU_BUILD_META="${COSIM_DIR}/.local/cosim/build/qemu-${QEMU_VERSION}/.cosim-build-meta"
QEMU_BUILD_SCRIPT="${SCRIPT_DIR}/cosim_build.sh"

PROFILE="host"
JSON_STDOUT=0
OUTPUT_DIR=""
GENERATED_AT="$(date -Iseconds)"
STRICT_ACCEPTANCE="${COSIM_STRICT_ACCEPTANCE:-0}"
umask 077

declare -a CHECK_IDS=()
declare -a CHECK_STATUSES=()
declare -a CHECK_REQUIRED=()
declare -a CHECK_SUMMARIES=()
declare -a CHECK_DETAILS=()

usage() {
    cat <<EOF
Strict cosim prerequisite audit (read-only).

Usage: $0 [host|build|run] [--json] [--output-dir DIR]
       $0 --profile host|build|run [--json] [--output-dir DIR]

Profiles:
  host   Linux, resources, KVM, Docker, proxy state, and download endpoints
  build  host checks plus source pins, build commands, and QEMU libraries
  run    runtime host checks plus pinned QEMU, gem5, guest assets, and image

Options:
  --json              Emit only the JSON report on stdout
  --output-dir DIR    Also write preflight.txt and preflight.json below
                      ${ARTIFACT_ROOT}/
  -h, --help          Show this help

Any required check with status FAIL or UNKNOWN makes the command exit 1.
Invalid arguments or an unsafe output directory make it exit 2.
EOF
}

die_usage() {
    echo "ERROR: $*" >&2
    usage >&2
    exit 2
}

redact_text() {
    local value="$1"

    value="${value//$'\r'/ }"
    value="${value//$'\n'/ }"
    # Mask URI userinfo if a future check accidentally includes a URL.
    printf '%s' "$value" | sed -E \
        's#([A-Za-z][A-Za-z0-9+.-]*://)[^/@[:space:]]+@#\1<redacted>@#g'
}

add_check() {
    local id="$1"
    local status="$2"
    local required="$3"
    local summary="$4"
    local detail="${5:-}"

    case "$status" in
        PASS|FAIL|UNKNOWN|WARN) ;;
        *) echo "internal error: invalid check status: $status" >&2; exit 2 ;;
    esac
    [[ "$required" == "true" || "$required" == "false" ]] || {
        echo "internal error: invalid required flag: $required" >&2
        exit 2
    }

    CHECK_IDS+=("$id")
    CHECK_STATUSES+=("$status")
    CHECK_REQUIRED+=("$required")
    CHECK_SUMMARIES+=("$(redact_text "$summary")")
    CHECK_DETAILS+=("$(redact_text "$detail")")
}

check_command() {
    local command_name="$1"
    local required="${2:-true}"
    local command_path

    if command_path="$(command -v "$command_name" 2>/dev/null)"; then
        add_check "command.${command_name}" PASS "$required" \
            "command is available" "$command_path"
    else
        add_check "command.${command_name}" FAIL "$required" \
            "command is missing" "install ${command_name} before continuing"
    fi
}

check_regular_file() {
    local id="$1"
    local path="$2"
    local minimum_size="${3:-1}"
    local require_executable="${4:-false}"
    local size

    if [[ ! -f "$path" || ! -r "$path" ]]; then
        add_check "$id" FAIL true "required file is missing or unreadable" "$path"
        return
    fi
    if [[ "$require_executable" == "true" && ! -x "$path" ]]; then
        add_check "$id" FAIL true "required file is not executable" "$path"
        return
    fi
    size="$(stat -c '%s' -- "$path" 2>/dev/null || true)"
    if [[ ! "$size" =~ ^[0-9]+$ ]]; then
        add_check "$id" UNKNOWN true "could not determine file size" "$path"
    elif (( size < minimum_size )); then
        add_check "$id" FAIL true "file is smaller than expected" \
            "path=${path}; bytes=${size}; minimum=${minimum_size}"
    else
        add_check "$id" PASS true "required file is ready" \
            "path=${path}; bytes=${size}"
    fi
}

metadata_value() {
    local file="$1"
    local key="$2"
    awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' \
        "$file" 2>/dev/null || true
}

metadata_has_single_keys() {
    local file="$1"
    shift
    local key count value

    [[ -f "$file" && -r "$file" && ! -L "$file" ]] || return 1
    for key in "$@"; do
        count="$(awk -F= -v wanted="$key" \
            '$1 == wanted {count++} END {print count + 0}' "$file")"
        [[ "$count" -eq 1 ]] || return 1
        value="$(metadata_value "$file" "$key")"
        [[ -n "$value" ]] || return 1
    done
}

metadata_has_exact_keys() {
    local file="$1"
    shift
    local populated_lines

    metadata_has_single_keys "$file" "$@" || return 1
    populated_lines="$(awk 'NF {count++} END {print count + 0}' "$file")"
    [[ "$populated_lines" -eq "$#" ]]
}

metadata_has_exact_assignment_keys() {
    local file="$1"
    shift
    local expected_count="$#"

    metadata_has_single_keys "$file" "$@" || return 1
    awk -v expected="$expected_count" '
        /^[[:space:]]*($|#)/ { next }
        /^[A-Za-z_][A-Za-z0-9_]*=/ { count++; next }
        { invalid = 1 }
        END {
            if (invalid || count != expected) {
                exit 1
            }
        }
    ' "$file"
}

is_rfc3339nano() {
    local value="$1"

    python3 - "$value" <<'PY' >/dev/null 2>&1
import datetime
import re
import sys

match = re.fullmatch(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})T"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})",
    sys.argv[1],
)
if match is None:
    raise SystemExit(1)
zone = match.group("zone")
if zone != "Z" and (int(zone[1:3]) > 23 or int(zone[4:6]) > 59):
    raise SystemExit(1)
try:
    datetime.datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
    )
except ValueError:
    raise SystemExit(1)
PY
}

tracked_head_file_sha256() {
    local repo_root="$1"
    local file="$2"
    local canonical_root canonical_file relative head_sha worktree_sha

    canonical_root="$(realpath -e -- "$repo_root")" || return 1
    canonical_file="$(realpath -e -- "$file")" || return 1
    [[ ! -L "$file" ]] || return 1
    case "$canonical_file" in
        "${canonical_root}/"*) relative="${canonical_file#"${canonical_root}/"}" ;;
        *) return 1 ;;
    esac
    git -C "$canonical_root" ls-files --error-unmatch -- "$relative" \
        >/dev/null 2>&1 || return 1
    worktree_sha="$(sha256sum -- "$canonical_file" | awk '{print $1}')" || return 1
    head_sha="$(git -C "$canonical_root" show "HEAD:${relative}" | \
        sha256sum | awk '{print $1}')" || return 1
    [[ "$head_sha" =~ ^[0-9a-f]{64}$ && "$head_sha" == "$worktree_sha" ]] || \
        return 1
    printf '%s' "$head_sha"
}

qemu_directory_fingerprint() {
    local build_script="$1"
    local source_dir="$2"

    bash -c 'source "$1"; directory_fingerprint "$2"' \
        _ "$build_script" "$source_dir"
}

qemu_array_fingerprint() {
    local build_script="$1"
    shift

    bash -c 'source "$1"; shift; array_fingerprint "$@"' \
        _ "$build_script" "$@"
}

validate_qemu_provenance() {
    local repo_root="$1"
    local qemu_bin="$2"
    local qemu_img="$3"
    local qemu_meta="$4"
    local toolchain_lock="$5"
    local source_dir="$6"
    local build_script="$7"
    local expected_prefix="${repo_root}/.local/cosim/qemu/${QEMU_VERSION}"
    local expected_qemu="${expected_prefix}/bin/qemu-system-x86_64"
    local expected_qemu_img="${expected_prefix}/bin/qemu-img"
    local expected_meta="${repo_root}/.local/cosim/build/qemu-${QEMU_VERSION}/.cosim-build-meta"
    local expected_lock="${repo_root}/configs/cosim/toolchain.lock"
    local expected_source_dir="${repo_root}/.local/cosim/src/qemu-${QEMU_VERSION}"
    local lock_schema_ok=true meta_schema_ok=true
    local lock_head_sha="" lock_worktree_sha="" meta_sha=""
    local lock_version="" lock_source_url="" lock_signature_url=""
    local lock_signing_key="" lock_signing_key_url="" lock_source_sha=""
    local lock_source_fingerprint=""
    local meta_version="" meta_source_url="" meta_source_sha=""
    local meta_signature_url="" meta_signing_key="" meta_signing_verified=""
    local meta_initial_source_fingerprint="" meta_source_fingerprint=""
    local meta_source_pristine="" meta_configure_fingerprint=""
    local meta_build_fingerprint="" meta_configure_args=""
    local meta_binary="" meta_binary_sha="" meta_qemu_img=""
    local meta_qemu_img_sha="" meta_timestamp=""
    local actual_source_fingerprint="" actual_binary_sha="" actual_qemu_img_sha=""
    local expected_configure_fingerprint="" expected_build_fingerprint=""
    local expected_configure_args_display="" quoted_arg="" problem failure_detail=""
    local -a problems=()
    local -a lock_keys=(
        QEMU_VERSION
        QEMU_SOURCE_URL
        QEMU_SIGNATURE_URL
        QEMU_RELEASE_KEY_FINGERPRINT
        QEMU_RELEASE_KEY_URL
        QEMU_SOURCE_SHA256
        QEMU_SOURCE_FINGERPRINT
    )
    local -a meta_keys=(
        version
        source_url
        source_sha256
        signature_url
        signing_key
        signing_verified
        initial_source_fingerprint
        source_fingerprint
        source_pristine
        configure_fingerprint
        build_fingerprint
        configure_args
        binary
        binary_sha256
        qemu_img
        qemu_img_sha256
        compiler
        timestamp
    )
    local -a configure_args=(
        "--prefix=${expected_prefix}"
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

    [[ "$qemu_meta" == "$expected_meta" && "$toolchain_lock" == "$expected_lock" && \
       "$source_dir" == "$expected_source_dir" ]] || \
        problems+=("QEMU provenance 输入不是仓库固定路径")

    if ! metadata_has_exact_assignment_keys "$toolchain_lock" "${lock_keys[@]}"; then
        lock_schema_ok=false
        problems+=("toolchain.lock 缺失、符号链接或 assignment schema 不精确")
    fi
    if lock_head_sha="$(tracked_head_file_sha256 "$repo_root" "$toolchain_lock")"; then
        lock_worktree_sha="$(sha256sum -- "$toolchain_lock" | awk '{print $1}')"
    else
        problems+=("toolchain.lock 未被 HEAD 跟踪或当前内容不等于 HEAD blob")
    fi
    if [[ "$lock_schema_ok" == "true" ]]; then
        lock_version="$(metadata_value "$toolchain_lock" QEMU_VERSION)"
        lock_source_url="$(metadata_value "$toolchain_lock" QEMU_SOURCE_URL)"
        lock_signature_url="$(metadata_value "$toolchain_lock" QEMU_SIGNATURE_URL)"
        lock_signing_key="$(metadata_value "$toolchain_lock" QEMU_RELEASE_KEY_FINGERPRINT)"
        lock_signing_key_url="$(metadata_value "$toolchain_lock" QEMU_RELEASE_KEY_URL)"
        lock_source_sha="$(metadata_value "$toolchain_lock" QEMU_SOURCE_SHA256)"
        lock_source_fingerprint="$(metadata_value \
            "$toolchain_lock" QEMU_SOURCE_FINGERPRINT)"
        [[ "$lock_version" == "$QEMU_VERSION" && \
           "$lock_source_url" == "$QEMU_SOURCE_URL" && \
           "$lock_signature_url" == "$QEMU_SIGNATURE_URL" && \
           "$lock_signing_key" == "$QEMU_RELEASE_KEY_FINGERPRINT" && \
           "$lock_signing_key_url" == "$QEMU_RELEASE_KEY_URL" && \
           "$lock_source_sha" =~ ^[0-9a-f]{64}$ && \
           "$lock_source_fingerprint" =~ ^[0-9a-f]{64}$ ]] || \
            problems+=("toolchain.lock 的 QEMU 版本、官方 URL、签名密钥、源码 SHA-256 或源码 fingerprint 无效")
    fi

    if ! metadata_has_exact_keys "$qemu_meta" "${meta_keys[@]}"; then
        meta_schema_ok=false
        problems+=("QEMU 构建 metadata 缺失、符号链接或 18 字段 schema 不精确")
    fi
    if [[ "$meta_schema_ok" == "true" ]]; then
        meta_sha="$(sha256sum -- "$qemu_meta" | awk '{print $1}')"
        meta_version="$(metadata_value "$qemu_meta" version)"
        meta_source_url="$(metadata_value "$qemu_meta" source_url)"
        meta_source_sha="$(metadata_value "$qemu_meta" source_sha256)"
        meta_signature_url="$(metadata_value "$qemu_meta" signature_url)"
        meta_signing_key="$(metadata_value "$qemu_meta" signing_key)"
        meta_signing_verified="$(metadata_value "$qemu_meta" signing_verified)"
        meta_initial_source_fingerprint="$(metadata_value "$qemu_meta" initial_source_fingerprint)"
        meta_source_fingerprint="$(metadata_value "$qemu_meta" source_fingerprint)"
        meta_source_pristine="$(metadata_value "$qemu_meta" source_pristine)"
        meta_configure_fingerprint="$(metadata_value "$qemu_meta" configure_fingerprint)"
        meta_build_fingerprint="$(metadata_value "$qemu_meta" build_fingerprint)"
        meta_configure_args="$(metadata_value "$qemu_meta" configure_args)"
        meta_binary="$(metadata_value "$qemu_meta" binary)"
        meta_binary_sha="$(metadata_value "$qemu_meta" binary_sha256)"
        meta_qemu_img="$(metadata_value "$qemu_meta" qemu_img)"
        meta_qemu_img_sha="$(metadata_value "$qemu_meta" qemu_img_sha256)"
        meta_timestamp="$(metadata_value "$qemu_meta" timestamp)"
    fi

    if [[ -x "$qemu_bin" && -f "$qemu_bin" && ! -L "$qemu_bin" && \
          "$(realpath -e -- "$qemu_bin" 2>/dev/null || true)" == "$expected_qemu" ]]; then
        actual_binary_sha="$(sha256sum -- "$qemu_bin" | awk '{print $1}')"
    else
        problems+=("qemu-system-x86_64 不是仓库固定的普通可执行文件")
    fi
    if [[ -x "$qemu_img" && -f "$qemu_img" && ! -L "$qemu_img" && \
          "$(realpath -e -- "$qemu_img" 2>/dev/null || true)" == "$expected_qemu_img" ]]; then
        actual_qemu_img_sha="$(sha256sum -- "$qemu_img" | awk '{print $1}')"
    else
        problems+=("qemu-img 不是仓库固定的普通可执行文件")
    fi

    if [[ -d "$source_dir" && ! -L "$source_dir" ]]; then
        actual_source_fingerprint="$(qemu_directory_fingerprint \
            "$build_script" "$source_dir" 2>/dev/null || true)"
    fi
    [[ "$actual_source_fingerprint" =~ ^[0-9a-f]{64}$ ]] || \
        problems+=("无法重算 QEMU 源码树 fingerprint")
    expected_configure_fingerprint="$(qemu_array_fingerprint \
        "$build_script" "${configure_args[@]}" 2>/dev/null || true)"
    for problem in "${configure_args[@]}"; do
        printf -v quoted_arg '%q' "$problem"
        expected_configure_args_display+="${expected_configure_args_display:+ }${quoted_arg}"
    done
    if [[ "$lock_source_sha" =~ ^[0-9a-f]{64}$ && \
          "$lock_source_fingerprint" =~ ^[0-9a-f]{64}$ && \
          "$actual_source_fingerprint" == "$lock_source_fingerprint" && \
          "$expected_configure_fingerprint" =~ ^[0-9a-f]{64}$ ]]; then
        expected_build_fingerprint="$(qemu_array_fingerprint "$build_script" \
            "$lock_source_sha" "$lock_source_fingerprint" \
            "$expected_configure_fingerprint" 2>/dev/null || true)"
    fi

    [[ "$meta_version" == "$lock_version" && \
       "$meta_source_url" == "$lock_source_url" && \
       "$meta_source_sha" == "$lock_source_sha" && \
       "$meta_signature_url" == "$lock_signature_url" && \
       "$meta_signing_key" == "$lock_signing_key" ]] || \
        problems+=("QEMU metadata 与 toolchain.lock 的源码及签名身份不一致")
    [[ "$meta_signing_verified" == "true" ]] || \
        problems+=("QEMU metadata 未记录 signing_verified=true")
    [[ "$meta_source_pristine" == "true" && \
       "$lock_source_fingerprint" =~ ^[0-9a-f]{64}$ && \
       "$meta_initial_source_fingerprint" == "$lock_source_fingerprint" && \
       "$meta_source_fingerprint" == "$lock_source_fingerprint" && \
       "$actual_source_fingerprint" == "$lock_source_fingerprint" ]] || \
        problems+=("QEMU lock、初始 metadata、当前 metadata 与 live 源码 fingerprint 不一致")
    [[ "$expected_configure_fingerprint" =~ ^[0-9a-f]{64}$ && \
       "$meta_configure_fingerprint" == "$expected_configure_fingerprint" && \
       "$meta_configure_args" == "$expected_configure_args_display" ]] || \
        problems+=("QEMU configure fingerprint 或参数不等于固定 recipe")
    [[ "$expected_build_fingerprint" =~ ^[0-9a-f]{64}$ && \
       "$meta_build_fingerprint" == "$expected_build_fingerprint" ]] || \
        problems+=("QEMU build fingerprint 不等于锁定 recipe")
    [[ "$meta_binary" == "$expected_qemu" && \
       "$meta_binary_sha" =~ ^[0-9a-f]{64}$ && \
       "$meta_binary_sha" == "$actual_binary_sha" ]] || \
        problems+=("qemu-system-x86_64 路径或 SHA-256 与 metadata 不一致")
    [[ "$meta_qemu_img" == "$expected_qemu_img" && \
       "$meta_qemu_img_sha" =~ ^[0-9a-f]{64}$ && \
       "$meta_qemu_img_sha" == "$actual_qemu_img_sha" ]] || \
        problems+=("qemu-img 路径或 SHA-256 与 metadata 不一致")
    is_rfc3339nano "$meta_timestamp" || \
        problems+=("QEMU metadata timestamp 不是合法 RFC3339Nano")

    if (( ${#problems[@]} > 0 )); then
        for problem in "${problems[@]}"; do
            failure_detail+="${failure_detail:+; }${problem}"
        done
        printf '%s' "$failure_detail"
        return 1
    fi

    printf '%s' \
        "metadata=${qemu_meta}; metadata_sha256=${meta_sha}; " \
        "toolchain_lock=${toolchain_lock}; toolchain_lock_sha256=${lock_worktree_sha}; " \
        "source_fingerprint=${actual_source_fingerprint}; locked_source_fingerprint=${lock_source_fingerprint}; " \
        "configure_fingerprint=${expected_configure_fingerprint}; " \
        "build_fingerprint=${expected_build_fingerprint}; " \
        "qemu_binary_sha256=${actual_binary_sha}; " \
        "qemu_img_sha256=${actual_qemu_img_sha}; timestamp=${meta_timestamp}"
}

check_host_core() {
    local kernel arch distro="unknown"
    kernel="$(uname -s 2>/dev/null || true)"
    arch="$(uname -m 2>/dev/null || true)"

    if [[ "$kernel" == "Linux" ]]; then
        add_check host.linux PASS true "Linux host detected" \
            "kernel=$(uname -r 2>/dev/null || echo unknown)"
    elif [[ -z "$kernel" ]]; then
        add_check host.linux UNKNOWN true "could not identify the host kernel"
    else
        add_check host.linux FAIL true "Linux is required" "detected=${kernel}"
    fi

    if [[ "$arch" == "x86_64" ]]; then
        add_check host.arch PASS true "x86_64 host architecture detected" "$arch"
    elif [[ -z "$arch" ]]; then
        add_check host.arch UNKNOWN true "could not identify the host architecture"
    else
        add_check host.arch FAIL true "x86_64 host architecture is required" \
            "detected=${arch}"
    fi

    if [[ -r /etc/os-release ]]; then
        distro="$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n 1 | tr -d '"')"
        add_check host.distribution PASS false "Linux distribution identified" \
            "${distro:-unknown}"
    else
        add_check host.distribution WARN false "Linux distribution metadata is unavailable"
    fi

    if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
        add_check host.virtualization PASS false "WSL Linux environment detected" \
            "Linux-visible resources are checked directly"
    else
        add_check host.virtualization PASS false "native or non-WSL Linux environment detected"
    fi

    local memory_kib memory_bytes minimum_memory_bytes=$((12 * 1024 * 1024 * 1024))
    memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
    if [[ "$memory_kib" =~ ^[0-9]+$ ]]; then
        memory_bytes=$((memory_kib * 1024))
        if (( memory_bytes >= minimum_memory_bytes )); then
            add_check host.memory PASS true "host memory meets the 12 GiB minimum" \
                "bytes=${memory_bytes}"
        else
            add_check host.memory FAIL true "host memory is below the 12 GiB minimum" \
                "bytes=${memory_bytes}; minimum=${minimum_memory_bytes}"
        fi
    else
        add_check host.memory UNKNOWN true "could not determine total host memory"
    fi

    local disk_bytes minimum_disk_bytes=$((80 * 1024 * 1024 * 1024))
    disk_bytes="$(df -PB1 "$COSIM_DIR" 2>/dev/null | awk 'NR == 2 {print $4}')"
    if [[ "$disk_bytes" =~ ^[0-9]+$ ]]; then
        if (( disk_bytes >= minimum_disk_bytes )); then
            add_check host.disk PASS true "workspace has at least 80 GiB free" \
                "available_bytes=${disk_bytes}"
        else
            add_check host.disk FAIL true "workspace has less than 80 GiB free" \
                "available_bytes=${disk_bytes}; minimum=${minimum_disk_bytes}"
        fi
    else
        add_check host.disk UNKNOWN true "could not determine workspace free space" \
            "$COSIM_DIR"
    fi

    local cpu_count
    cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
    if [[ "$cpu_count" =~ ^[0-9]+$ ]] && (( cpu_count >= 2 )); then
        add_check host.cpus PASS true "at least two host CPUs are visible" \
            "online_cpus=${cpu_count}"
    elif [[ "$cpu_count" =~ ^[0-9]+$ ]]; then
        add_check host.cpus FAIL true "fewer than two host CPUs are visible" \
            "online_cpus=${cpu_count}"
    else
        add_check host.cpus UNKNOWN true "could not determine online CPU count"
    fi

    local kvm_device="${COSIM_KVM_DEVICE:-/dev/kvm}"
    if [[ -c "$kvm_device" ]]; then
        add_check host.kvm_node PASS true "KVM character device exists" "$kvm_device"
        if [[ -r "$kvm_device" && -w "$kvm_device" ]]; then
            add_check host.kvm_access PASS true "current process can access KVM" \
                "$kvm_device"
        else
            add_check host.kvm_access FAIL true "current process cannot read and write KVM" \
                "refresh membership in the kvm group, then rerun"
        fi
    elif [[ -e "$kvm_device" ]]; then
        add_check host.kvm_node FAIL true "KVM path is not a character device" "$kvm_device"
        add_check host.kvm_access UNKNOWN true "KVM access was not tested" "$kvm_device"
    else
        add_check host.kvm_node FAIL true "KVM character device is missing" "$kvm_device"
        add_check host.kvm_access UNKNOWN true "KVM access was not tested" "$kvm_device"
    fi

    if [[ -d /dev/shm && -w /dev/shm ]]; then
        local shm_bytes
        shm_bytes="$(df -PB1 /dev/shm 2>/dev/null | awk 'NR == 2 {print $4}')"
        add_check host.dev_shm PASS true "/dev/shm exists and is writable" \
            "available_bytes=${shm_bytes:-unknown}; sparse cosim backings are supported"
    elif [[ -d /dev/shm ]]; then
        add_check host.dev_shm FAIL true "/dev/shm is not writable by the current process"
    else
        add_check host.dev_shm FAIL true "/dev/shm is unavailable"
    fi

    if [[ -d /tmp && -w /tmp ]]; then
        add_check host.tmp PASS true "/tmp exists and is writable"
    else
        add_check host.tmp FAIL true "/tmp must be writable for cosim sockets"
    fi

    check_command docker true
    if command -v docker >/dev/null 2>&1; then
        local docker_version docker_arch
        if docker_version="$(docker info --format '{{.ServerVersion}}' 2>/dev/null)"; then
            docker_arch="$(docker info --format '{{.Architecture}}' 2>/dev/null || true)"
            add_check host.docker_daemon PASS true "Docker daemon is reachable by the current process" \
                "server=${docker_version:-unknown}; architecture=${docker_arch:-unknown}"
            if [[ "$docker_arch" == "x86_64" || "$docker_arch" == "amd64" ]]; then
                add_check host.docker_arch PASS true "Docker daemon uses an amd64-compatible architecture" \
                    "${docker_arch}"
            elif [[ -z "$docker_arch" ]]; then
                add_check host.docker_arch UNKNOWN true "could not determine Docker architecture"
            else
                add_check host.docker_arch FAIL true "Docker daemon architecture is incompatible" \
                    "detected=${docker_arch}"
            fi
        else
            add_check host.docker_daemon FAIL true \
                "Docker daemon is unavailable or access is denied" \
                "start Docker and refresh membership in the docker group"
            add_check host.docker_arch UNKNOWN true "Docker architecture was not tested"
        fi
    else
        add_check host.docker_daemon UNKNOWN true "Docker daemon was not tested"
        add_check host.docker_arch UNKNOWN true "Docker architecture was not tested"
    fi

    local proxy_detail=""
    local proxy_name proxy_state
    for proxy_name in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
                      http_proxy https_proxy all_proxy no_proxy; do
        if [[ -n "${!proxy_name:-}" ]]; then
            proxy_state="set (value and credentials redacted)"
        else
            proxy_state="unset"
        fi
        proxy_detail+="${proxy_name}=${proxy_state}; "
    done
    add_check host.proxy PASS false "proxy environment was recorded without URL values" \
        "${proxy_detail%'; '}"
}

check_network_endpoint() {
    local id="$1"
    local url="$2"
    local accepted_codes="$3"
    local code curl_status=0

    if ! command -v curl >/dev/null 2>&1; then
        add_check "$id" UNKNOWN true "network endpoint was not checked because curl is missing"
        return
    fi
    if [[ "${COSIM_PREFLIGHT_SKIP_NETWORK:-0}" == "1" ]]; then
        add_check "$id" UNKNOWN true "network endpoint check was explicitly skipped"
        return
    fi

    if code="$(curl --silent --show-error --location --head \
        --connect-timeout 5 --max-time 15 \
        --retry 2 --retry-delay 1 --retry-all-errors \
        --output /dev/null \
        --write-out '%{http_code}' "$url" 2>/dev/null)"; then
        curl_status=0
    else
        curl_status=$?
    fi
    if (( curl_status != 0 )); then
        add_check "$id" FAIL true "HTTPS 端点请求失败" \
            "curl_exit=${curl_status}; http_status=${code:-unavailable}"
    elif [[ "$code" =~ $accepted_codes ]]; then
        add_check "$id" PASS true "HTTPS endpoint is reachable" "http_status=${code}"
    elif [[ "$code" == "000" || -z "$code" ]]; then
        add_check "$id" FAIL true "HTTPS endpoint is unreachable" \
            "check DNS, TLS, firewall, and the redacted proxy environment"
    else
        add_check "$id" FAIL true "HTTPS endpoint returned an unexpected status" \
            "http_status=${code}"
    fi
}

check_download_environment() {
    local packer_url packer_qemu_plugin_url ubuntu_iso_url
    local guest_kernel_image_deb_url guest_kernel_modules_deb_url
    local guest_kernel_modules_extra_deb_url guest_kernel_headers_deb_url
    local guest_kernel_headers_generic_deb_url

    packer_url="$(metadata_value "$GUEST_LOCK" PACKER_URL)"
    packer_qemu_plugin_url="$(metadata_value "$GUEST_LOCK" PACKER_QEMU_PLUGIN_URL)"
    ubuntu_iso_url="$(metadata_value "$GUEST_LOCK" UBUNTU_ISO_URL)"
    guest_kernel_image_deb_url="$(metadata_value "$GUEST_LOCK" GUEST_KERNEL_IMAGE_DEB_URL)"
    guest_kernel_modules_deb_url="$(metadata_value "$GUEST_LOCK" GUEST_KERNEL_MODULES_DEB_URL)"
    guest_kernel_modules_extra_deb_url="$(metadata_value "$GUEST_LOCK" GUEST_KERNEL_MODULES_EXTRA_DEB_URL)"
    guest_kernel_headers_deb_url="$(metadata_value "$GUEST_LOCK" GUEST_KERNEL_HEADERS_DEB_URL)"
    guest_kernel_headers_generic_deb_url="$(metadata_value "$GUEST_LOCK" GUEST_KERNEL_HEADERS_GENERIC_DEB_URL)"

    check_command git true
    check_command curl true
    # curl 会跟随重定向，因此只接受最终响应状态。
    check_network_endpoint network.github https://github.com '^200$'
    check_network_endpoint network.qemu https://download.qemu.org '^200$'
    check_network_endpoint network.packer "$packer_url" '^(200|206)$'
    check_network_endpoint network.packer_qemu_plugin "$packer_qemu_plugin_url" \
        '^(200|206)$'
    check_network_endpoint network.ubuntu_iso "$ubuntu_iso_url" \
        '^(200|206)$'
    check_network_endpoint network.guest_kernel_image_deb \
        "$guest_kernel_image_deb_url" '^(200|206)$'
    check_network_endpoint network.guest_kernel_modules_deb \
        "$guest_kernel_modules_deb_url" '^(200|206)$'
    check_network_endpoint network.guest_kernel_modules_extra_deb \
        "$guest_kernel_modules_extra_deb_url" '^(200|206)$'
    check_network_endpoint network.guest_kernel_headers_deb \
        "$guest_kernel_headers_deb_url" '^(200|206)$'
    check_network_endpoint network.guest_kernel_headers_generic_deb \
        "$guest_kernel_headers_generic_deb_url" \
        '^(200|206)$'
    check_network_endpoint network.amdgpu \
        https://repo.radeon.com/amdgpu/7.0/ubuntu/dists/noble/InRelease \
        '^200$'
    check_network_endpoint network.rocm \
        https://repo.radeon.com/rocm/apt/7.0/dists/noble/InRelease \
        '^200$'
    # Registry authentication challenges (401) prove the endpoint is reachable.
    check_network_endpoint network.ghcr https://ghcr.io/v2/ '^(200|401|405)$'
}

check_submodule() {
    local name="$1"
    local path="${COSIM_DIR}/${name}"
    local expected actual

    expected="$(git -C "$COSIM_DIR" ls-tree HEAD -- "$name" 2>/dev/null | \
        awk '{print $3}' || true)"
    if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
        add_check "source.${name}" UNKNOWN true "could not read the recorded gitlink" "$name"
        return
    fi
    if [[ ! -e "${path}/.git" ]]; then
        add_check "source.${name}" FAIL true "submodule is not initialized" \
            "path=${path}; expected=${expected}"
        return
    fi
    actual="$(git -C "$path" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$actual" == "$expected" ]]; then
        add_check "source.${name}" PASS true "submodule matches the recorded gitlink" \
            "commit=${actual}"
    elif [[ "$actual" =~ ^[0-9a-f]{40}$ ]]; then
        add_check "source.${name}" FAIL true "submodule does not match the recorded gitlink" \
            "expected=${expected}; actual=${actual}"
    else
        add_check "source.${name}" UNKNOWN true "submodule HEAD could not be read" "$path"
    fi
}

check_pkg_config_module() {
    local module="$1"
    if ! command -v pkg-config >/dev/null 2>&1; then
        add_check "library.${module}" UNKNOWN true \
            "library was not checked because pkg-config is missing"
    elif pkg-config --exists "$module" >/dev/null 2>&1; then
        add_check "library.${module}" PASS true "QEMU build library is available" \
            "module=${module}; version=$(pkg-config --modversion "$module" 2>/dev/null || echo unknown)"
    else
        add_check "library.${module}" FAIL true "QEMU build library is missing" \
            "pkg-config module=${module}"
    fi
}

check_build_profile() {
    local command_name
    for command_name in gpg tar xz sha256sum make gcc g++ pkg-config python3 \
                        bison flex ninja jq unzip shellcheck socat rsync screen \
                        timeout stdbuf setsid; do
        check_command "$command_name" true
    done

    local module
    for module in glib-2.0 pixman-1 slirp liburing libseccomp libzstd; do
        check_pkg_config_module "$module"
    done

    if [[ -r /usr/include/libaio.h || -r /usr/include/x86_64-linux-gnu/libaio.h ]]; then
        add_check library.libaio PASS true "libaio development header is available"
    else
        add_check library.libaio FAIL true "libaio development header is missing" \
            "install the distribution libaio development package"
    fi

    check_submodule gem5
    check_submodule gem5-resources

    if [[ -w "$COSIM_DIR" ]]; then
        add_check build.workspace PASS true "repository workspace is writable" "$COSIM_DIR"
    else
        add_check build.workspace FAIL true "repository workspace is not writable" "$COSIM_DIR"
    fi
}

select_qemu_binary() {
    local local_qemu="${COSIM_DIR}/.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64"
    if [[ -n "${QEMU_BIN:-}" ]]; then
        printf '%s' "$QEMU_BIN"
    elif [[ -x "$local_qemu" ]]; then
        printf '%s' "$local_qemu"
    else
        command -v qemu-system-x86_64 2>/dev/null || true
    fi
}

select_qemu_img_binary() {
    local qemu_bin="$1"

    if [[ -n "${QEMU_IMG:-}" ]]; then
        printf '%s' "$QEMU_IMG"
    elif [[ -n "$qemu_bin" ]]; then
        printf '%s/qemu-img' "$(dirname "$qemu_bin")"
    fi
}

check_qemu_runtime() {
    local qemu_bin qemu_img version qemu_provenance_detail
    qemu_bin="$(select_qemu_binary)"
    if [[ -z "$qemu_bin" || ! -x "$qemu_bin" ]]; then
        add_check run.qemu_binary FAIL true "qemu-system-x86_64 is missing" \
            "expected local toolchain: ${COSIM_DIR}/.local/cosim/qemu/10.1.5"
        add_check run.qemu_version UNKNOWN true "QEMU version was not tested"
        add_check run.qemu_vfio_user UNKNOWN true "QEMU vfio-user-pci support was not tested"
        add_check run.qemu_q35 UNKNOWN true "QEMU q35 support was not tested"
        add_check run.qemu_kvm UNKNOWN true "QEMU KVM accelerator support was not tested"
        add_check run.qemu_img UNKNOWN true "qemu-img was not tested"
        add_check run.qemu_provenance FAIL true \
            "QEMU provenance validation failed" \
            "qemu-system-x86_64 缺失，无法验证锁文件、构建 metadata 与二进制"
        return
    fi

    add_check run.qemu_binary PASS true "QEMU runtime binary is executable" "$qemu_bin"
    version="$($qemu_bin --version 2>/dev/null | head -n 1 || true)"
    if [[ "$version" == *"QEMU emulator version 10.1.5"* ]]; then
        add_check run.qemu_version PASS true "QEMU version matches the pinned toolchain" "$version"
    elif [[ -n "$version" ]]; then
        add_check run.qemu_version FAIL true "QEMU does not match pinned version 10.1.5" "$version"
    else
        add_check run.qemu_version UNKNOWN true "QEMU version could not be read" "$qemu_bin"
    fi

    if "$qemu_bin" -device help 2>/dev/null | grep -F 'vfio-user-pci' >/dev/null; then
        add_check run.qemu_vfio_user PASS true "QEMU provides vfio-user-pci"
    else
        add_check run.qemu_vfio_user FAIL true "QEMU does not provide vfio-user-pci"
    fi
    if "$qemu_bin" -machine help 2>/dev/null | grep -F 'q35' >/dev/null; then
        add_check run.qemu_q35 PASS true "QEMU provides a q35 machine"
    else
        add_check run.qemu_q35 FAIL true "QEMU does not provide a q35 machine"
    fi
    if "$qemu_bin" -accel help 2>/dev/null | grep -F 'kvm' >/dev/null; then
        add_check run.qemu_kvm PASS true "QEMU provides the KVM accelerator"
    else
        add_check run.qemu_kvm FAIL true "QEMU does not provide the KVM accelerator"
    fi

    qemu_img="$(select_qemu_img_binary "$qemu_bin")"
    if [[ -x "$qemu_img" ]]; then
        add_check run.qemu_img PASS true "qemu-img is installed beside QEMU" "$qemu_img"
    else
        add_check run.qemu_img FAIL true "qemu-img is missing beside QEMU" "$qemu_img"
    fi

    if qemu_provenance_detail="$(validate_qemu_provenance \
        "$COSIM_DIR" "$qemu_bin" "$qemu_img" "$QEMU_BUILD_META" \
        "$TOOLCHAIN_LOCK" "$QEMU_SOURCE_DIR" "$QEMU_BUILD_SCRIPT")"; then
        add_check run.qemu_provenance PASS true \
            "QEMU lock、源码 recipe、构建 metadata 与两个二进制一致" \
            "$qemu_provenance_detail"
    else
        add_check run.qemu_provenance FAIL true \
            "QEMU provenance validation failed" "$qemu_provenance_detail"
    fi
}

check_guest_provenance() {
    local resources_dir="$1"
    local disk_image="$2"
    local kernel="$3"
    local m5_binary="$4"
    local qemu_bin qemu_img run_id detail status required report_path=""
    local -a command

    qemu_bin="$(select_qemu_binary)"
    qemu_img="$(select_qemu_img_binary "$qemu_bin")"
    run_id="${COSIM_RUN_ID:-preflight}"
    required=false
    if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
        required=true
    fi
    command=(
        python3 "$GUEST_PROVENANCE_VALIDATOR" verify
        --repo-root "$COSIM_DIR"
        --resources-dir "$resources_dir"
        --metadata "$GUEST_META"
        --seal "$GUEST_SEAL"
        --guest-lock "$GUEST_LOCK"
        --guest-patch "$GUEST_PATCH"
        --image "$disk_image"
        --kernel "$kernel"
        --m5 "$m5_binary"
        --qemu-bin "$qemu_bin"
        --qemu-img "$qemu_img"
        --run-id "$run_id"
    )
    if [[ -n "$OUTPUT_DIR" ]]; then
        report_path="${OUTPUT_DIR}/guest-provenance.json"
        command+=(--output "$report_path")
    fi

    if detail="$("${command[@]}" 2>&1)"; then
        add_check run.guest_provenance PASS "$required" \
            "Guest metadata, content seal, source recipe, kernel, and m5 agree" \
            "report=${report_path:-stdout}; image=sealed-stat; kernel=full-sha256; m5=full-sha256"
        return
    fi

    status=WARN
    if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
        status=FAIL
    fi
    add_check run.guest_provenance "$status" "$required" \
        "Guest provenance validation failed" "$detail"
}

check_run_profile() {
    local gem5_dir="${COSIM_DIR}/gem5"
    local resources_dir="${COSIM_DIR}/gem5-resources"
    local gem5_bin="${gem5_dir}/build/VEGA_X86/gem5.opt"
    local gem5_meta="${gem5_dir}/build/VEGA_X86/.cosim-build-meta"
    local gem5_lock_rel="configs/cosim/gem5-baseline.lock"
    local gem5_config="${gem5_dir}/configs/example/gpufs/mi300_cosim.py"
    local resource_root="${resources_dir}/src/x86-ubuntu-gpu-ml"
    local disk_image="${resource_root}/disk-image/x86-ubuntu-rocm70"
    local kernel="${resource_root}/vmlinux-rocm70"
    local m5_binary="${resource_root}/files/m5"
    local guest_setup="${resource_root}/files/cosim-gpu-setup.sh"

    check_command screen true
    check_command socat true
    check_command rsync true
    check_command timeout true
    check_command stdbuf true
    check_command setsid true
    check_submodule gem5
    check_submodule gem5-resources
    check_qemu_runtime

    check_regular_file run.gem5_binary "$gem5_bin" 1 true
    check_regular_file run.gem5_config "$gem5_config" 1 false

    local image_name="${GEM5_DOCKER_IMAGE:-gem5-run:local}"
    local image_id=""
    if ! command -v docker >/dev/null 2>&1; then
        add_check run.docker_image UNKNOWN true "runtime Docker image was not checked"
    else
        image_id="$(docker image inspect --format '{{.Id}}' "$image_name" 2>/dev/null || true)"
        if [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
            add_check run.docker_image PASS true "gem5 runtime Docker image exists" \
                "image=${image_name}; id=${image_id}"
        else
            add_check run.docker_image FAIL true "gem5 runtime Docker image is missing" \
                "image=${image_name}"
        fi
    fi

    local provenance_ok=true
    local -a provenance_failures=()
    local actual_commit="" actual_fingerprint="" actual_binary_sha="" gem5_status=""
    local repo_status=""
    local meta_commit="" meta_algorithm="" meta_fingerprint="" meta_target=""
    local meta_binary="" meta_binary_sha="" meta_docker_image=""
    local lock_schema="" lock_commit="" lock_algorithm="" lock_fingerprint=""
    local lock_binary_sha="" lock_docker_image=""
    local lock_head_sha="" lock_worktree_sha=""

    if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
        if ! git -C "$COSIM_DIR" ls-files --error-unmatch -- "$gem5_lock_rel" \
            >/dev/null 2>&1 || \
           ! git -C "$COSIM_DIR" diff --quiet HEAD -- "$gem5_lock_rel"; then
            provenance_ok=false
            provenance_failures+=("baseline lock is not tracked and identical to HEAD")
        fi
        lock_head_sha="$(git -C "$COSIM_DIR" show "HEAD:${gem5_lock_rel}" 2>/dev/null | \
            sha256sum | awk '{print $1}' || true)"
        lock_worktree_sha="$(sha256sum "$GEM5_BASELINE_LOCK" 2>/dev/null | \
            awk '{print $1}' || true)"
        [[ "$lock_head_sha" =~ ^[0-9a-f]{64}$ && \
           "$lock_head_sha" == "$lock_worktree_sha" ]] || {
            provenance_ok=false
            provenance_failures+=("baseline lock content does not match its HEAD blob")
        }
    fi
    if metadata_has_exact_keys "$GEM5_BASELINE_LOCK" schema gem5_commit \
        source_fingerprint_algorithm source_fingerprint binary_sha256 docker_image; then
        lock_schema="$(metadata_value "$GEM5_BASELINE_LOCK" schema)"
        lock_commit="$(metadata_value "$GEM5_BASELINE_LOCK" gem5_commit)"
        lock_algorithm="$(metadata_value "$GEM5_BASELINE_LOCK" source_fingerprint_algorithm)"
        lock_fingerprint="$(metadata_value "$GEM5_BASELINE_LOCK" source_fingerprint)"
        lock_binary_sha="$(metadata_value "$GEM5_BASELINE_LOCK" binary_sha256)"
        lock_docker_image="$(metadata_value "$GEM5_BASELINE_LOCK" docker_image)"
    else
        provenance_ok=false
        provenance_failures+=("baseline lock is missing, symlinked, or malformed")
    fi

    if metadata_has_single_keys "$gem5_meta" commit source_fingerprint_algorithm \
        source_fingerprint target binary binary_sha256 docker_image; then
        meta_commit="$(metadata_value "$gem5_meta" commit)"
        meta_algorithm="$(metadata_value "$gem5_meta" source_fingerprint_algorithm)"
        meta_fingerprint="$(metadata_value "$gem5_meta" source_fingerprint)"
        meta_target="$(metadata_value "$gem5_meta" target)"
        meta_binary="$(metadata_value "$gem5_meta" binary)"
        meta_binary_sha="$(metadata_value "$gem5_meta" binary_sha256)"
        meta_docker_image="$(metadata_value "$gem5_meta" docker_image)"
    else
        provenance_ok=false
        provenance_failures+=("build metadata is missing, symlinked, or malformed")
    fi

    actual_commit="$(git -C "$gem5_dir" rev-parse HEAD 2>/dev/null || true)"
    repo_status="$(git -C "$COSIM_DIR" -c status.renames=false \
        status --porcelain=v1 --untracked-files=all --ignore-submodules=none \
        2>/dev/null || true)"
    if [[ "$STRICT_ACCEPTANCE" == "1" && -n "$repo_status" ]]; then
        provenance_ok=false
        provenance_failures+=("top-level source tree is not clean")
    fi
    gem5_status="$(git -C "$gem5_dir" -c status.renames=false \
        status --porcelain=v1 --untracked-files=all --ignore-submodules=none \
        2>/dev/null || true)"
    if [[ "$STRICT_ACCEPTANCE" == "1" && -n "$gem5_status" ]]; then
        provenance_ok=false
        provenance_failures+=("gem5 source tree is not clean")
    fi
    actual_fingerprint="$(bash -c 'source "$1"; source_fingerprint "$2"' \
        _ "${SCRIPT_DIR}/cosim_build.sh" "$gem5_dir" 2>/dev/null || true)"
    actual_binary_sha="$(sha256sum -- "$gem5_bin" 2>/dev/null | awk '{print $1}' || true)"

    [[ "$lock_schema" == "1" && "$lock_algorithm" == "2" && \
       "$lock_commit" =~ ^[0-9a-f]{40}$ && \
       "$lock_fingerprint" =~ ^[0-9a-f]{64}$ && \
       "$lock_binary_sha" =~ ^[0-9a-f]{64}$ && \
       "$lock_docker_image" =~ ^sha256:[0-9a-f]{64}$ ]] || {
        provenance_ok=false
        provenance_failures+=("baseline lock values are invalid")
    }
    [[ "$actual_fingerprint" =~ ^[0-9a-f]{64}$ ]] || {
        provenance_ok=false
        provenance_failures+=("current source fingerprint could not be computed")
    }
    [[ "$meta_target" == "VEGA_X86" && "$meta_binary" == "$gem5_bin" ]] || {
        provenance_ok=false
        provenance_failures+=("build metadata does not select the canonical target and binary")
    }
    [[ "$lock_commit" == "$meta_commit" && "$meta_commit" == "$actual_commit" && \
       "$lock_algorithm" == "$meta_algorithm" && \
       "$lock_fingerprint" == "$meta_fingerprint" && \
       "$meta_fingerprint" == "$actual_fingerprint" && \
       "$lock_binary_sha" == "$meta_binary_sha" && \
       "$meta_binary_sha" == "$actual_binary_sha" && \
       "$lock_docker_image" == "$meta_docker_image" && \
       "$meta_docker_image" == "$image_id" ]] || {
        provenance_ok=false
        provenance_failures+=("lock, metadata, source, binary, and runtime image do not match")
    }

    if [[ "$provenance_ok" == "true" ]]; then
        add_check run.gem5_provenance PASS true \
            "gem5 trust anchor matches the current source, binary, metadata, and runtime image" \
            "$GEM5_BASELINE_LOCK"
    else
        local provenance_detail
        provenance_detail="$(IFS='; '; printf '%s' "${provenance_failures[*]}")"
        add_check run.gem5_provenance FAIL true \
            "gem5 trust anchor validation failed" "$provenance_detail"
    fi

    if [[ "$STRICT_ACCEPTANCE" == "1" ]]; then
        add_check run.strict_acceptance PASS true \
            "strict acceptance source policy is enabled" "COSIM_STRICT_ACCEPTANCE=1"
    else
        add_check run.strict_acceptance PASS true \
            "replayable development source policy is enabled" "COSIM_STRICT_ACCEPTANCE=0"
    fi

    check_regular_file run.disk_image "$disk_image" $((1024 * 1024 * 1024)) false
    check_regular_file run.kernel "$kernel" $((1024 * 1024)) false
    check_regular_file run.m5 "$m5_binary" 1 true
    # 这里检查的是 Packer source template，而不是安装后的 Guest executable。
    # rocm-install.sh 会先赋予上传副本执行权限，再进行安装。
    check_regular_file run.guest_setup "$guest_setup" 1 false
    check_guest_provenance "$resources_dir" "$disk_image" "$kernel" "$m5_binary"

    local kernel_count
    kernel_count="$(find "${COSIM_DIR}/tests/kernels" -maxdepth 1 -type f -name '*.cpp' \
        2>/dev/null | wc -l || true)"
    if [[ "$kernel_count" =~ ^[0-9]+$ ]] && (( kernel_count > 0 )); then
        add_check run.test_sources PASS true "HIP regression sources are present" \
            "count=${kernel_count}"
    else
        add_check run.test_sources FAIL true "no HIP regression sources were found"
    fi

    local stale_count=0
    while IFS= read -r _; do
        stale_count=$((stale_count + 1))
    done < <(find /tmp -maxdepth 1 -type s -name 'gem5-mi300x*.sock' 2>/dev/null)
    while IFS= read -r _; do
        stale_count=$((stale_count + 1))
    done < <(find /dev/shm -maxdepth 1 -type f \
        \( -name 'mi300x-vram*' -o -name 'cosim-guest-ram*' \) 2>/dev/null)
    if (( stale_count == 0 )); then
        add_check run.stale_resources PASS false "no stale socket or shared-memory files were found"
    else
        add_check run.stale_resources WARN false "possible prior cosim resources were found" \
            "count=${stale_count}; inspect with scripts/cosim_cleanup.sh"
    fi
}

json_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\t'/\\t}"
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/\\r}"
    printf '%s' "$value"
}

required_failure_count() {
    local index failures=0
    for index in "${!CHECK_IDS[@]}"; do
        if [[ "${CHECK_REQUIRED[$index]}" == "true" && \
              ( "${CHECK_STATUSES[$index]}" == "FAIL" || \
                "${CHECK_STATUSES[$index]}" == "UNKNOWN" ) ]]; then
            failures=$((failures + 1))
        fi
    done
    printf '%s' "$failures"
}

render_text() {
    local failures="$1"
    local overall="PASS"
    local index requirement
    (( failures == 0 )) || overall="FAIL"

    printf 'Cosim preflight: profile=%s overall=%s generated_at=%s\n' \
        "$PROFILE" "$overall" "$GENERATED_AT"
    for index in "${!CHECK_IDS[@]}"; do
        if [[ "${CHECK_REQUIRED[$index]}" == "true" ]]; then
            requirement="required"
        else
            requirement="advisory"
        fi
        printf '[%s] %-9s %s - %s\n' \
            "${CHECK_STATUSES[$index]}" "$requirement" \
            "${CHECK_IDS[$index]}" "${CHECK_SUMMARIES[$index]}"
        if [[ -n "${CHECK_DETAILS[$index]}" ]]; then
            printf '  %s\n' "${CHECK_DETAILS[$index]}"
        fi
    done
    printf 'Required failures or unknowns: %s\n' "$failures"
}

render_json() {
    local failures="$1"
    local overall="PASS"
    local index comma=""
    (( failures == 0 )) || overall="FAIL"

    printf '{\n'
    printf '  "schema": "cosim-preflight-v1",\n'
    printf '  "profile": "%s",\n' "$(json_escape "$PROFILE")"
    printf '  "generated_at": "%s",\n' "$(json_escape "$GENERATED_AT")"
    printf '  "repo_root": "%s",\n' "$(json_escape "$COSIM_DIR")"
    printf '  "overall_status": "%s",\n' "$overall"
    printf '  "required_failure_count": %s,\n' "$failures"
    printf '  "checks": [\n'
    for index in "${!CHECK_IDS[@]}"; do
        printf '%s' "$comma"
        printf '    {"id":"%s","status":"%s","required":%s,"summary":"%s","detail":"%s"}' \
            "$(json_escape "${CHECK_IDS[$index]}")" \
            "${CHECK_STATUSES[$index]}" \
            "${CHECK_REQUIRED[$index]}" \
            "$(json_escape "${CHECK_SUMMARIES[$index]}")" \
            "$(json_escape "${CHECK_DETAILS[$index]}")"
        comma=$',\n'
    done
    printf '\n  ]\n}\n'
}

validate_output_dir() {
    local requested="$1"
    local resolved
    resolved="$(realpath -m -- "$requested")" || die_usage \
        "could not resolve --output-dir: $requested"
    case "$resolved" in
        "${ARTIFACT_ROOT}/"*) ;;
        *) die_usage "--output-dir must be below ${ARTIFACT_ROOT}" ;;
    esac
    OUTPUT_DIR="$resolved"
}

# 合同测试只加载纯校验函数；直接执行脚本时才解析参数并运行 profile。
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        host|build|run)
            PROFILE="$1"
            shift
            ;;
        --profile)
            [[ $# -ge 2 ]] || die_usage "--profile requires a value"
            PROFILE="$2"
            shift 2
            ;;
        --json)
            JSON_STDOUT=1
            shift
            ;;
        --output-dir)
            [[ $# -ge 2 ]] || die_usage "--output-dir requires a value"
            validate_output_dir "$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die_usage "unknown argument: $1"
            ;;
    esac
done

case "$PROFILE" in
    host|build|run) ;;
    *) die_usage "invalid profile: $PROFILE" ;;
esac
[[ "$STRICT_ACCEPTANCE" == "0" || "$STRICT_ACCEPTANCE" == "1" ]] || \
    die_usage "COSIM_STRICT_ACCEPTANCE must be 0 or 1"

check_host_core
case "$PROFILE" in
    host)
        check_download_environment
        ;;
    build)
        check_download_environment
        check_build_profile
        ;;
    run)
        check_run_profile
        ;;
esac

failure_count="$(required_failure_count)"

if [[ -n "$OUTPUT_DIR" ]]; then
    umask 077
    mkdir -p "$OUTPUT_DIR"
    render_text "$failure_count" > "${OUTPUT_DIR}/preflight.txt"
    render_json "$failure_count" > "${OUTPUT_DIR}/preflight.json"
fi

if [[ "$JSON_STDOUT" -eq 1 ]]; then
    render_json "$failure_count"
else
    render_text "$failure_count"
fi

if (( failure_count > 0 )); then
    exit 1
fi
