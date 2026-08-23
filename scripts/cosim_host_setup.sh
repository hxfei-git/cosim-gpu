#!/bin/bash
# Audit, describe, install, and verify the host prerequisites for cosim-gpu.
#
# This script deliberately does not invoke sudo or handle credentials.  Run the
# install action as root; the other actions are read-only and work unprivileged.

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
readonly -a REQUIRED_PACKAGES=(
    ca-certificates
    curl
    git
    gpg
    tar
    xz-utils
    patch
    file
    binutils
    build-essential
    python3
    python3-venv
    ninja-build
    pkg-config
    bison
    flex
    libglib2.0-dev
    libpixman-1-dev
    zlib1g-dev
    docker.io
    unzip
    wget
    rsync
    jq
    shellcheck
    socat
    screen
    pciutils
    strace
    gdb
    libslirp-dev
    libaio-dev
    liburing-dev
    libseccomp-dev
    libzstd-dev
)

ACTION=""
TARGET_USER=""
GRANT_RUNTIME_GROUPS=0

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} {audit|plan|install|verify} --for-user USER [--grant-runtime-groups]

Actions:
  audit    Print a read-only host inventory. Missing prerequisites are reported
           but do not make the audit fail.
  plan     Print the fixed privileged operations without executing them.
  install  Install the fixed package set and start Docker. This action must be
           run as root. Group membership is unchanged unless explicitly enabled.
  verify   Check packages, Docker, KVM, and USER access; exit nonzero on failure.

The script never reads or changes WSL/Windows configuration, proxy settings,
sudoers, or credentials. Existing shells do not acquire newly added groups;
start a new login session (or use an equivalent group-reexec) after install.

--grant-runtime-groups adds USER to docker and kvm. Membership in docker is
security-sensitive and effectively grants root-equivalent host control.
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

parse_args() {
    [[ "$#" -gt 0 ]] || {
        usage >&2
        exit 2
    }

    case "$1" in
        audit|plan|install|verify)
            ACTION="$1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown action: $1"
            ;;
    esac

    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --for-user)
                [[ -z "$TARGET_USER" ]] || die "--for-user may be specified only once"
                [[ "$#" -ge 2 && -n "$2" ]] || die "--for-user requires a value"
                TARGET_USER="$2"
                shift 2
                ;;
            --grant-runtime-groups)
                GRANT_RUNTIME_GROUPS=1
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "unknown argument: $1"
                ;;
        esac
    done

    [[ -n "$TARGET_USER" ]] || die "--for-user USER is required"
    getent passwd "$TARGET_USER" >/dev/null || die "user does not exist: $TARGET_USER"
    [[ "$(id -u "$TARGET_USER")" -ne 0 ]] || die "--for-user must name a non-root runtime user"
}

package_is_installed() {
    local package="$1"
    dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii '
}

account_has_group() {
    local user="$1"
    local required_group="$2"
    local group

    while IFS= read -r group; do
        [[ "$group" == "$required_group" ]] && return 0
    done < <(id -nG "$user" | tr ' ' '\n')
    return 1
}

print_group_activation_note() {
    cat <<EOF
group_activation_note=Account membership and current-process membership are different.
group_activation_note=Existing shells and this Codex process do not inherit group changes.
group_activation_note=Open a new login session, or re-exec under docker and kvm groups, before launching cosim.
EOF
}

audit_host() {
    local package status
    local os_id="unknown"
    local os_version="unknown"
    local kernel_release
    local init_name="unknown"
    local mem_kib="unknown"
    local swap_kib="unknown"
    local workspace_free_kib="unknown"
    local shm_kib="unknown"

    if [[ -r /etc/os-release ]]; then
        # Values are only used as data in quoted output.
        # shellcheck disable=SC1091
        source /etc/os-release
        os_id="${ID:-unknown}"
        os_version="${VERSION_ID:-unknown}"
    fi
    kernel_release="$(uname -r)"
    [[ -r /proc/1/comm ]] && init_name="$(tr -d '\n' < /proc/1/comm)"
    [[ -r /proc/meminfo ]] && mem_kib="$(awk '$1 == "MemTotal:" {print $2}' /proc/meminfo)"
    [[ -r /proc/meminfo ]] && swap_kib="$(awk '$1 == "SwapTotal:" {print $2}' /proc/meminfo)"
    workspace_free_kib="$(df -Pk . | awk 'NR == 2 {print $4}')"
    shm_kib="$(df -Pk /dev/shm 2>/dev/null | awk 'NR == 2 {print $2}' || true)"

    echo "audit_version=1"
    echo "os_id=${os_id}"
    echo "os_version=${os_version}"
    echo "architecture=$(uname -m)"
    echo "kernel=${kernel_release}"
    if [[ "${kernel_release,,}" == *microsoft* || "${kernel_release,,}" == *wsl* ]]; then
        echo "environment=wsl"
    else
        echo "environment=linux"
    fi
    echo "pid1=${init_name}"
    echo "mem_total_kib=${mem_kib}"
    echo "swap_total_kib=${swap_kib}"
    echo "workspace_free_kib=${workspace_free_kib}"
    echo "dev_shm_total_kib=${shm_kib:-unknown}"
    echo "target_user=${TARGET_USER}"

    for package in "${REQUIRED_PACKAGES[@]}"; do
        status="missing"
        package_is_installed "$package" && status="installed"
        echo "package.${package}=${status}"
    done

    if [[ -c /dev/kvm ]]; then
        echo "kvm.device=present"
        echo "kvm.permissions=$(stat -c '%A:%U:%G' /dev/kvm)"
    else
        echo "kvm.device=missing"
    fi

    if account_has_group "$TARGET_USER" kvm; then
        echo "account_group.kvm=present"
    else
        echo "account_group.kvm=missing"
    fi
    if account_has_group "$TARGET_USER" docker; then
        echo "account_group.docker=present"
    else
        echo "account_group.docker=missing"
    fi

    if command -v systemctl >/dev/null 2>&1; then
        echo "docker.service_active=$(systemctl is-active docker 2>/dev/null || true)"
        echo "docker.service_enabled=$(systemctl is-enabled docker 2>/dev/null || true)"
    else
        echo "docker.service_active=systemctl-missing"
        echo "docker.service_enabled=systemctl-missing"
    fi
    if command -v docker >/dev/null 2>&1; then
        echo "docker.command=present"
    else
        echo "docker.command=missing"
    fi

    print_group_activation_note
}

print_plan() {
    printf 'target_user=%s\n' "$TARGET_USER"
    printf 'privileged_step=apt-get update\n'
    printf 'privileged_step=apt-get install --no-install-recommends'
    printf ' %q' "${REQUIRED_PACKAGES[@]}"
    printf '\n'
    printf 'privileged_step=systemctl enable --now docker\n'
    if [[ "$GRANT_RUNTIME_GROUPS" -eq 1 ]]; then
        printf 'privileged_step=usermod -aG docker,kvm %q\n' "$TARGET_USER"
        printf 'security_note=docker group is root-equivalent\n'
    else
        printf 'deferred_step=runtime group membership remains unchanged\n'
    fi
    printf 'credential_policy=caller obtains root; this script neither invokes sudo nor handles credentials\n'
    print_group_activation_note
}

install_host() {
    [[ "$EUID" -eq 0 ]] || die "install must be run as root"
    command -v apt-get >/dev/null 2>&1 || die "apt-get is required"
    command -v systemctl >/dev/null 2>&1 || die "systemctl is required"
    command -v usermod >/dev/null 2>&1 || die "usermod is required"

    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        "${REQUIRED_PACKAGES[@]}"

    getent group kvm >/dev/null || die "kvm group is missing after package installation"
    getent group docker >/dev/null || die "docker group is missing after package installation"
    systemctl enable --now docker
    if [[ "$GRANT_RUNTIME_GROUPS" -eq 1 ]]; then
        usermod -aG docker,kvm "$TARGET_USER"
    fi

    echo "Host prerequisite installation completed for ${TARGET_USER}."
    if [[ "$GRANT_RUNTIME_GROUPS" -eq 0 ]]; then
        echo "Runtime group membership was deliberately left unchanged."
    fi
    print_group_activation_note
}

verify_host() {
    local failures=0
    local package

    verify_pass() {
        echo "PASS: $*"
    }
    verify_fail() {
        echo "FAIL: $*" >&2
        failures=$((failures + 1))
    }

    if [[ "$(uname -s)" == "Linux" ]]; then
        verify_pass "Linux host"
    else
        verify_fail "Linux host required"
    fi
    if [[ -r /proc/1/comm && "$(tr -d '\n' < /proc/1/comm)" == "systemd" ]]; then
        verify_pass "systemd is PID 1"
    else
        verify_fail "systemd must be PID 1"
    fi

    for package in "${REQUIRED_PACKAGES[@]}"; do
        if package_is_installed "$package"; then
            verify_pass "package installed: ${package}"
        else
            verify_fail "package missing: ${package}"
        fi
    done

    if [[ -c /dev/kvm ]]; then
        verify_pass "/dev/kvm exists"
    else
        verify_fail "/dev/kvm is missing"
    fi
    if account_has_group "$TARGET_USER" kvm; then
        verify_pass "${TARGET_USER} has kvm account membership"
    else
        verify_fail "${TARGET_USER} lacks kvm account membership"
    fi
    if account_has_group "$TARGET_USER" docker; then
        verify_pass "${TARGET_USER} has docker account membership"
    else
        verify_fail "${TARGET_USER} lacks docker account membership"
    fi

    if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet docker 2>/dev/null; then
        verify_pass "Docker service is enabled"
    else
        verify_fail "Docker service is not enabled"
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet docker 2>/dev/null; then
        verify_pass "Docker service is active"
    else
        verify_fail "Docker service is not active"
    fi
    if command -v docker >/dev/null 2>&1; then
        verify_pass "docker command is available"
    else
        verify_fail "docker command is unavailable"
    fi

    if command -v runuser >/dev/null 2>&1 && [[ "$EUID" -eq 0 ]]; then
        if runuser -u "$TARGET_USER" -- test -r /dev/kvm -a -w /dev/kvm; then
            verify_pass "${TARGET_USER} can read and write /dev/kvm in a fresh login context"
        else
            verify_fail "${TARGET_USER} cannot read and write /dev/kvm in a fresh login context"
        fi
        if command -v docker >/dev/null 2>&1 && \
           runuser -u "$TARGET_USER" -- docker info >/dev/null 2>&1; then
            verify_pass "${TARGET_USER} can reach Docker in a fresh login context"
        else
            verify_fail "${TARGET_USER} cannot reach Docker in a fresh login context"
        fi
    else
        echo "INFO: root-only fresh-login access probes were not run"
    fi

    print_group_activation_note
    if [[ "$failures" -ne 0 ]]; then
        echo "VERIFY_RESULT=FAIL failures=${failures}" >&2
        return 1
    fi
    echo "VERIFY_RESULT=PASS"
}

main() {
    parse_args "$@"
    case "$ACTION" in
        audit) audit_host ;;
        plan) print_plan ;;
        install) install_host ;;
        verify) verify_host ;;
    esac
}

main "$@"
