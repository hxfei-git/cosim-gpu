#!/bin/bash
# Strict, read-only preflight gate for the QEMU + gem5 MI300X workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
ARTIFACT_ROOT="${COSIM_DIR}/artifacts"

PROFILE="host"
JSON_STDOUT=0
OUTPUT_DIR=""
GENERATED_AT="$(date -Iseconds)"

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
    local code

    if ! command -v curl >/dev/null 2>&1; then
        add_check "$id" UNKNOWN true "network endpoint was not checked because curl is missing"
        return
    fi
    if [[ "${COSIM_PREFLIGHT_SKIP_NETWORK:-0}" == "1" ]]; then
        add_check "$id" UNKNOWN true "network endpoint check was explicitly skipped"
        return
    fi

    code="$(curl --silent --show-error --location --head \
        --connect-timeout 5 --max-time 15 \
        --retry 2 --retry-delay 1 --retry-all-errors \
        --output /dev/null \
        --write-out '%{http_code}' "$url" 2>/dev/null || true)"
    if [[ "$code" =~ $accepted_codes ]]; then
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
    check_command git true
    check_command curl true
    check_network_endpoint network.github https://github.com '^(200|301|302)$'
    check_network_endpoint network.qemu https://download.qemu.org '^(200|301|302)$'
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

check_qemu_runtime() {
    local qemu_bin qemu_dir qemu_img version
    qemu_bin="$(select_qemu_binary)"
    if [[ -z "$qemu_bin" || ! -x "$qemu_bin" ]]; then
        add_check run.qemu_binary FAIL true "qemu-system-x86_64 is missing" \
            "expected local toolchain: ${COSIM_DIR}/.local/cosim/qemu/10.1.5"
        add_check run.qemu_version UNKNOWN true "QEMU version was not tested"
        add_check run.qemu_vfio_user UNKNOWN true "QEMU vfio-user-pci support was not tested"
        add_check run.qemu_q35 UNKNOWN true "QEMU q35 support was not tested"
        add_check run.qemu_kvm UNKNOWN true "QEMU KVM accelerator support was not tested"
        add_check run.qemu_img UNKNOWN true "qemu-img was not tested"
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

    qemu_dir="$(dirname "$qemu_bin")"
    qemu_img="${qemu_dir}/qemu-img"
    if [[ -x "$qemu_img" ]]; then
        add_check run.qemu_img PASS true "qemu-img is installed beside QEMU" "$qemu_img"
    else
        add_check run.qemu_img FAIL true "qemu-img is missing beside QEMU" "$qemu_img"
    fi

    local qemu_meta="${COSIM_DIR}/.local/cosim/build/qemu-10.1.5/.cosim-build-meta"
    if [[ -r "$qemu_meta" ]]; then
        local meta_version meta_source_sha meta_binary meta_binary_sha actual_binary_sha
        meta_version="$(metadata_value "$qemu_meta" version)"
        meta_source_sha="$(metadata_value "$qemu_meta" source_sha256)"
        meta_binary="$(metadata_value "$qemu_meta" binary)"
        meta_binary_sha="$(metadata_value "$qemu_meta" binary_sha256)"
        actual_binary_sha="$(sha256sum -- "$qemu_bin" 2>/dev/null | awk '{print $1}' || true)"
        if [[ "$meta_version" == "10.1.5" && \
              "$meta_source_sha" =~ ^[0-9a-f]{64}$ && \
              "$meta_binary" == "$qemu_bin" && \
              "$meta_binary_sha" =~ ^[0-9a-f]{64}$ && \
              "$meta_binary_sha" == "$actual_binary_sha" ]]; then
            add_check run.qemu_provenance PASS true \
                "QEMU provenance matches the selected binary" "$qemu_meta"
        else
            add_check run.qemu_provenance FAIL true \
                "QEMU provenance does not match the selected binary" "$qemu_meta"
        fi
    else
        add_check run.qemu_provenance FAIL true "QEMU provenance metadata is missing" "$qemu_meta"
    fi
}

check_run_profile() {
    local gem5_dir="${COSIM_DIR}/gem5"
    local resources_dir="${COSIM_DIR}/gem5-resources"
    local gem5_bin="${gem5_dir}/build/VEGA_X86/gem5.opt"
    local gem5_meta="${gem5_dir}/build/VEGA_X86/.cosim-build-meta"
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
    if [[ -r "$gem5_meta" && -f "$gem5_bin" ]]; then
        local meta_commit meta_fingerprint meta_binary_sha actual_commit actual_binary_sha
        meta_commit="$(metadata_value "$gem5_meta" commit)"
        meta_fingerprint="$(metadata_value "$gem5_meta" source_fingerprint)"
        meta_binary_sha="$(metadata_value "$gem5_meta" binary_sha256)"
        actual_commit="$(git -C "$gem5_dir" rev-parse HEAD 2>/dev/null || true)"
        actual_binary_sha="$(sha256sum -- "$gem5_bin" 2>/dev/null | awk '{print $1}' || true)"
        if [[ "$meta_commit" =~ ^[0-9a-f]{40}$ && \
              "$meta_commit" == "$actual_commit" && \
              "$meta_fingerprint" =~ ^[0-9a-f]{64}$ && \
              "$meta_binary_sha" =~ ^[0-9a-f]{64}$ && \
              "$meta_binary_sha" == "$actual_binary_sha" ]]; then
            add_check run.gem5_provenance PASS true \
                "gem5 provenance matches the selected source and binary" "$gem5_meta"
        else
            add_check run.gem5_provenance FAIL true \
                "gem5 provenance does not match the selected source or binary" "$gem5_meta"
        fi
    elif [[ -r "$gem5_meta" ]]; then
        add_check run.gem5_provenance FAIL true \
            "gem5 provenance cannot be verified without the binary" "$gem5_meta"
    else
        add_check run.gem5_provenance FAIL true "gem5 provenance metadata is missing" "$gem5_meta"
    fi

    check_regular_file run.disk_image "$disk_image" $((1024 * 1024 * 1024)) false
    check_regular_file run.kernel "$kernel" $((1024 * 1024)) false
    check_regular_file run.m5 "$m5_binary" 1 true
    # This is a Packer source template, not the installed Guest executable.
    # rocm-install.sh marks the uploaded copy executable before installing it.
    check_regular_file run.guest_setup "$guest_setup" 1 false

    local image_name="${GEM5_DOCKER_IMAGE:-gem5-run:local}"
    if ! command -v docker >/dev/null 2>&1; then
        add_check run.docker_image UNKNOWN true "runtime Docker image was not checked"
    else
        local image_id
        image_id="$(docker image inspect --format '{{.Id}}' "$image_name" 2>/dev/null || true)"
        if [[ -n "$image_id" ]]; then
            add_check run.docker_image PASS true "gem5 runtime Docker image exists" \
                "image=${image_name}; id=${image_id}"
        else
            add_check run.docker_image FAIL true "gem5 runtime Docker image is missing" \
                "image=${image_name}"
        fi
    fi

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
