#!/bin/bash
# Reproducible build entry point for the host QEMU toolchain, gem5, and m5.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
GEM5_DIR="${GEM5_DIR:-${COSIM_DIR}/gem5}"
RESOURCES_DIR="${GEM5_RESOURCES_DIR:-${COSIM_DIR}/gem5-resources}"
LOCAL_ROOT="${COSIM_LOCAL_ROOT:-${COSIM_DIR}/.local/cosim}"

TOOLCHAIN_LOCK="${COSIM_DIR}/configs/cosim/toolchain.lock"
GUEST_LOCK="${COSIM_DIR}/configs/cosim/guest.lock"
GUEST_PATCH="${SCRIPT_DIR}/patches/0002-guest-core-reproducible.patch"
QEMU_VERSION="10.1.5"
QEMU_RELEASE_KEY="CEACC9E15534EBABB82D3FA03353C9CEF108B584"
QEMU_URL="https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz"
QEMU_SIG_URL="${QEMU_URL}.sig"
QEMU_KEY_URL="https://keys.openpgp.org/vks/v1/by-fingerprint/${QEMU_RELEASE_KEY}"
QEMU_SOURCE_SHA256=""
QEMU_SOURCE_DIR="${LOCAL_ROOT}/src/qemu-${QEMU_VERSION}"
QEMU_SOURCE_PROVENANCE="${LOCAL_ROOT}/src/qemu-${QEMU_VERSION}.source-meta"
QEMU_BUILD_DIR="${LOCAL_ROOT}/build/qemu-${QEMU_VERSION}"
QEMU_PREFIX="${LOCAL_ROOT}/qemu/${QEMU_VERSION}"
QEMU_BIN="${QEMU_PREFIX}/bin/qemu-system-x86_64"
QEMU_IMG="${QEMU_PREFIX}/bin/qemu-img"
QEMU_META="${QEMU_BUILD_DIR}/.cosim-build-meta"

GEM5_BIN="${GEM5_DIR}/build/VEGA_X86/gem5.opt"
GEM5_META="${GEM5_DIR}/build/VEGA_X86/.cosim-build-meta"
GEM5_IMAGE="${GEM5_BUILD_IMAGE:-gem5-build:local}"
GEM5_RUN_IMAGE="${GEM5_RUN_IMAGE:-gem5-run:local}"
GEM5_DOCKERFILE="${SCRIPT_DIR}/Dockerfile.run"
SOURCE_FINGERPRINT_ALGORITHM=2

M5_BIN="${GEM5_DIR}/util/m5/build/x86/out/m5"
M5_GUEST_FILE="${RESOURCES_DIR}/src/x86-ubuntu-gpu-ml/files/m5"
M5_META="${LOCAL_ROOT}/m5-build.meta"
M5_RECIPE_VERSION=1

PACKER_VERSION="1.10.0"
PACKER_URL="https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_amd64.zip"
PACKER_SHA256="a8442e7041db0a7db48f468e353ee07fa6a7b35276ec62f60813c518ca3296c1"
PACKER_PLUGIN_VERSION="1.1.6"
PACKER_PLUGIN_URL="https://releases.hashicorp.com/packer-plugin-qemu/${PACKER_PLUGIN_VERSION}/packer-plugin-qemu_${PACKER_PLUGIN_VERSION}_linux_amd64.zip"
PACKER_PLUGIN_SHA256="3f735539fbdd0368785babda272b85738866f736415dce59d04b4cb550c4db87"
PACKER_ROOT="${LOCAL_ROOT}/packer/${PACKER_VERSION}"
PACKER_BIN="${PACKER_ROOT}/packer"
PACKER_PLUGIN_ROOT="${LOCAL_ROOT}/packer/plugins"
PACKER_CACHE_DIR="${LOCAL_ROOT}/packer/cache"
PACKER_CONFIG_ROOT="${LOCAL_ROOT}/packer/config"

GUEST_TEMPLATE_REL="src/x86-ubuntu-gpu-ml"
GUEST_BUILD_ROOT="${LOCAL_ROOT}/build/guest"
GUEST_META="${GUEST_BUILD_ROOT}/.cosim-build-meta"
GUEST_IMAGE="${RESOURCES_DIR}/${GUEST_TEMPLATE_REL}/disk-image/x86-ubuntu-rocm70"
GUEST_KERNEL_IMAGE="${RESOURCES_DIR}/${GUEST_TEMPLATE_REL}/vmlinux-rocm70"
GUEST_ARTIFACT_ROOT="${COSIM_DIR}/artifacts/amd-gpu-learning-env/build/guest"
GUEST_KERNEL_DEB_ROOT="${LOCAL_ROOT}/downloads/guest-kernel/6.8.0-79.79"
GUEST_KERNEL_DEB_KEYS=(
    GUEST_KERNEL_IMAGE_DEB
    GUEST_KERNEL_MODULES_DEB
    GUEST_KERNEL_MODULES_EXTRA_DEB
    GUEST_KERNEL_HEADERS_DEB
    GUEST_KERNEL_HEADERS_GENERIC_DEB
)
GUEST_KERNEL_DEB_FILES=()
GUEST_RECIPE_VERSION=1

QEMU_BUILD_JOBS="${QEMU_BUILD_JOBS:-4}"
GEM5_BUILD_JOBS="${GEM5_BUILD_JOBS:-4}"
M5_BUILD_JOBS="${M5_BUILD_JOBS:-4}"
FORCE=0

usage() {
    cat <<EOF
Usage: $0 {lock-qemu-source|qemu|gem5|m5|guest|all|status} [--force]

Environment:
  COSIM_LOCAL_ROOT   Local source/build/toolchain root
  QEMU_BUILD_JOBS    QEMU parallelism (default: 4)
  GEM5_BUILD_JOBS    gem5 parallelism (default: 4)
  M5_BUILD_JOBS      m5 parallelism (default: 4)
  GEM5_DIR           Alternate gem5 worktree
  GEM5_RESOURCES_DIR Alternate gem5-resources worktree

--force reruns the normal incremental build. It never deletes build trees.

QEMU source identity is fixed by configs/cosim/toolchain.lock. A missing
archive SHA-256 is a hard stop and is checked before any download starts.
Use lock-qemu-source once to verify the official detached signature and print
the SHA-256 that Codex must record in the tracked lock before building.
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

fingerprint_worktree_path() {
    local repo="$1"
    local path="$2"
    local full_path="${repo}/${path}"
    local digest mode

    printf 'worktree-path\0%s\0' "$path"
    if [[ -L "$full_path" ]]; then
        mode="$(stat -c '%f' -- "$full_path")" || return 1
        printf 'symlink\0%s\0' "$mode"
        readlink --zero -- "$full_path" || return 1
    elif [[ -f "$full_path" ]]; then
        mode="$(stat -c '%f' -- "$full_path")" || return 1
        digest="$(sha256sum < "$full_path" | awk '{print $1}')" || return 1
        printf 'file\0%s\0%s\0' "$mode" "$digest"
    elif [[ -d "$full_path" ]]; then
        mode="$(stat -c '%f' -- "$full_path")" || return 1
        printf 'directory\0%s\0' "$mode"
    elif [[ -e "$full_path" ]]; then
        mode="$(stat -c '%f' -- "$full_path")" || return 1
        printf 'other\0%s\0' "$mode"
    else
        printf 'missing\0'
    fi
}

source_fingerprint_stream() {
    local repo="$1"
    local head

    head="$(git -C "$repo" rev-parse HEAD)" || return 1
    printf 'source-fingerprint-algorithm\0%s\0head\0%s\0status\0' \
        "$SOURCE_FINGERPRINT_ALGORITHM" "$head"
    git -C "$repo" -c status.renames=false status --porcelain=v1 -z \
        --untracked-files=all --ignore-submodules=none || return 1
    printf '\0index\0'

    if ! git -C "$repo" ls-files --stage -z |
        while IFS= read -r -d '' entry; do
            local entry_metadata mode object stage path nested_fingerprint
            entry_metadata="${entry%%$'\t'*}"
            path="${entry#*$'\t'}"
            IFS=' ' read -r mode object stage <<< "$entry_metadata"
            [[ -n "$mode" && -n "$object" && -n "$stage" ]] || exit 1

            printf 'index-entry\0%s\0%s\0%s\0%s\0' \
                "$mode" "$object" "$stage" "$path"
            if [[ "$mode" == "160000" && "$stage" == "0" ]]; then
                if [[ -e "${repo}/${path}/.git" ]]; then
                    if ! nested_fingerprint="$(source_fingerprint "${repo}/${path}")"; then
                        echo "failed to fingerprint initialized gitlink: ${repo}/${path}" >&2
                        exit 1
                    fi
                    printf 'gitlink-initialized\0%s\0' "$nested_fingerprint"
                else
                    printf 'gitlink-uninitialized\0%s\0' "$object"
                fi
            else
                fingerprint_worktree_path "$repo" "$path" || exit 1
            fi
        done
    then
        echo "failed to enumerate tracked paths for source fingerprint: $repo" >&2
        return 1
    fi

    printf 'untracked\0'
    if ! git -C "$repo" ls-files --others --exclude-standard -z |
        while IFS= read -r -d '' path; do
            printf 'untracked-entry\0'
            fingerprint_worktree_path "$repo" "$path" || exit 1
        done
    then
        echo "failed to enumerate untracked paths for source fingerprint: $repo" >&2
        return 1
    fi
}

source_fingerprint() {
    local repo="$1"
    local fingerprint

    if ! fingerprint="$(source_fingerprint_stream "$repo" | sha256sum | awk '{print $1}')"; then
        return 1
    fi
    [[ "$fingerprint" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$fingerprint"
}

metadata_value() {
    local file="$1"
    local key="$2"
    [[ -f "$file" ]] || return 1
    sed -n "s/^${key}=//p" "$file" | head -n 1
}

write_metadata() {
    local file="$1"
    shift
    local tmp
    mkdir -p "$(dirname "$file")"
    tmp="$(mktemp "${file}.XXXXXX")"
    printf '%s\n' "$@" > "$tmp"
    mv "$tmp" "$file"
}

lock_value() {
    local key="$1"
    [[ -f "$TOOLCHAIN_LOCK" ]] || return 1
    sed -n "s/^${key}=//p" "$TOOLCHAIN_LOCK" | head -n 1
}

validate_qemu_lock() {
    local allow_empty_sha="${1:-false}"
    [[ -f "$TOOLCHAIN_LOCK" ]] || die "toolchain lock not found: $TOOLCHAIN_LOCK"
    [[ "$(lock_value QEMU_VERSION || true)" == "$QEMU_VERSION" ]] || \
        die "toolchain lock QEMU_VERSION must be ${QEMU_VERSION}"
    [[ "$(lock_value QEMU_SOURCE_URL || true)" == "$QEMU_URL" ]] || \
        die "toolchain lock QEMU_SOURCE_URL does not match the official URL"
    [[ "$(lock_value QEMU_SIGNATURE_URL || true)" == "$QEMU_SIG_URL" ]] || \
        die "toolchain lock QEMU_SIGNATURE_URL does not match the official URL"
    [[ "$(lock_value QEMU_RELEASE_KEY_FINGERPRINT || true)" == "$QEMU_RELEASE_KEY" ]] || \
        die "toolchain lock QEMU release-key fingerprint mismatch"
    [[ "$(lock_value QEMU_RELEASE_KEY_URL || true)" == "$QEMU_KEY_URL" ]] || \
        die "toolchain lock QEMU_RELEASE_KEY_URL does not match the official key URL"

    QEMU_SOURCE_SHA256="$(lock_value QEMU_SOURCE_SHA256 || true)"
    if [[ -z "$QEMU_SOURCE_SHA256" ]]; then
        [[ "$allow_empty_sha" == "true" ]] && return 0
        die "QEMU_SOURCE_SHA256 is empty in $TOOLCHAIN_LOCK; establish it from the GPG-verified official qemu-${QEMU_VERSION}.tar.xz before building"
    fi
    [[ "$QEMU_SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || die \
        "QEMU_SOURCE_SHA256 in $TOOLCHAIN_LOCK is not a lowercase SHA-256"
}

guest_lock_value() {
    local key="$1"
    [[ -f "$GUEST_LOCK" ]] || return 1
    sed -n "s/^${key}=//p" "$GUEST_LOCK" | head -n 1
}

validate_guest_lock() {
    local key url sha
    [[ -f "$GUEST_LOCK" ]] || die "guest lock not found: $GUEST_LOCK"
    [[ -f "$GUEST_PATCH" ]] || die "guest overlay patch not found: $GUEST_PATCH"
    [[ "$(guest_lock_value GUEST_LOCK_VERSION || true)" == "1" ]] || \
        die "unsupported guest lock version"
    [[ "$(guest_lock_value PACKER_VERSION || true)" == "$PACKER_VERSION" ]] || \
        die "guest lock Packer version mismatch"
    [[ "$(guest_lock_value PACKER_URL || true)" == "$PACKER_URL" ]] || \
        die "guest lock Packer URL mismatch"
    [[ "$(guest_lock_value PACKER_SHA256 || true)" == "$PACKER_SHA256" ]] || \
        die "guest lock Packer SHA-256 mismatch"
    [[ "$(guest_lock_value PACKER_QEMU_PLUGIN_VERSION || true)" == \
        "$PACKER_PLUGIN_VERSION" ]] || die "guest lock Packer QEMU plugin version mismatch"
    [[ "$(guest_lock_value PACKER_QEMU_PLUGIN_URL || true)" == \
        "$PACKER_PLUGIN_URL" ]] || die "guest lock Packer QEMU plugin URL mismatch"
    [[ "$(guest_lock_value PACKER_QEMU_PLUGIN_SHA256 || true)" == \
        "$PACKER_PLUGIN_SHA256" ]] || die "guest lock Packer QEMU plugin SHA-256 mismatch"
    [[ "$(guest_lock_value UBUNTU_ISO_URL || true)" == \
        "https://old-releases.ubuntu.com/releases/24.04.2/ubuntu-24.04.2-live-server-amd64.iso" ]] || \
        die "guest lock Ubuntu ISO URL mismatch"
    [[ "$(guest_lock_value UBUNTU_ISO_SHA256 || true)" == \
        "d6dab0c3a657988501b4bd76f1297c053df710e06e0c3aece60dead24f270b4d" ]] || \
        die "guest lock Ubuntu ISO SHA-256 mismatch"
    [[ "$(guest_lock_value ROCM_KEY_SHA256 || true)" == \
        "2de99e2354646a90d9903e2a669fc4e36b02c1bbff7075c481e12d7edab2c88b" ]] || \
        die "guest lock ROCm key SHA-256 mismatch"
    [[ "$(guest_lock_value AMDGPU_DKMS_VERSION || true)" == \
        "1:6.14.14.30100000-2204008.24.04" ]] || die "guest lock amdgpu-dkms version mismatch"
    [[ "$(guest_lock_value ROCM_VERSION || true)" == \
        "7.0.0.70000-38~24.04" ]] || die "guest lock ROCm version mismatch"
    [[ "$(guest_lock_value GUEST_KERNEL || true)" == "6.8.0-79-generic" ]] || \
        die "guest lock kernel mismatch"
    [[ "$(guest_lock_value GUEST_KERNEL_PACKAGE_VERSION || true)" == "6.8.0-79.79" ]] || \
        die "guest lock kernel package version mismatch"
    for key in "${GUEST_KERNEL_DEB_KEYS[@]}"; do
        url="$(guest_lock_value "${key}_URL" || true)"
        sha="$(guest_lock_value "${key}_SHA256" || true)"
        [[ "$url" =~ ^https://snapshot\.ubuntu\.com/ubuntu/[0-9]{8}T[0-9]{6}Z/pool/main/l/.+\.deb$ ]] || \
            die "guest lock ${key}_URL is not an official Ubuntu snapshot .deb URL"
        [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || \
            die "guest lock ${key}_SHA256 is not a lowercase SHA-256"
    done
}

download_verified_file() {
    local label="$1"
    local url="$2"
    local expected_sha="$3"
    local destination="$4"
    local actual_sha recovery

    mkdir -p "$(dirname "$destination")"
    if [[ -f "$destination" ]]; then
        actual_sha="$(sha256sum "$destination" | awk '{print $1}')"
        if [[ "$actual_sha" == "$expected_sha" ]]; then
            return 0
        fi
        recovery="${destination}.invalid.$(date +%Y%m%dT%H%M%S)"
        mv -- "$destination" "$recovery"
        echo "preserved ${label} with unexpected hash: $recovery" >&2
    fi

    curl --fail --location --proto '=https' --tlsv1.2 \
        --retry 3 --retry-delay 2 --retry-all-errors \
        --output "${destination}.part" "$url"
    actual_sha="$(sha256sum "${destination}.part" | awk '{print $1}')"
    [[ "$actual_sha" == "$expected_sha" ]] || {
        mv -- "${destination}.part" "${destination}.invalid.$(date +%Y%m%dT%H%M%S)"
        die "${label} SHA-256 mismatch"
    }
    mv -- "${destination}.part" "$destination"
}

prepare_guest_kernel_debs() {
    local key url sha filename destination package version architecture
    local -A expected_package=(
        [GUEST_KERNEL_IMAGE_DEB]="linux-image-6.8.0-79-generic"
        [GUEST_KERNEL_MODULES_DEB]="linux-modules-6.8.0-79-generic"
        [GUEST_KERNEL_MODULES_EXTRA_DEB]="linux-modules-extra-6.8.0-79-generic"
        [GUEST_KERNEL_HEADERS_DEB]="linux-headers-6.8.0-79"
        [GUEST_KERNEL_HEADERS_GENERIC_DEB]="linux-headers-6.8.0-79-generic"
    )
    local -A expected_architecture=(
        [GUEST_KERNEL_IMAGE_DEB]="amd64"
        [GUEST_KERNEL_MODULES_DEB]="amd64"
        [GUEST_KERNEL_MODULES_EXTRA_DEB]="amd64"
        [GUEST_KERNEL_HEADERS_DEB]="all"
        [GUEST_KERNEL_HEADERS_GENERIC_DEB]="amd64"
    )

    require_command curl
    require_command dpkg-deb
    GUEST_KERNEL_DEB_FILES=()
    for key in "${GUEST_KERNEL_DEB_KEYS[@]}"; do
        url="$(guest_lock_value "${key}_URL")"
        sha="$(guest_lock_value "${key}_SHA256")"
        filename="${url##*/}"
        destination="${GUEST_KERNEL_DEB_ROOT}/${filename}"
        download_verified_file "$key" "$url" "$sha" "$destination"
        package="$(dpkg-deb --field "$destination" Package)"
        version="$(dpkg-deb --field "$destination" Version)"
        architecture="$(dpkg-deb --field "$destination" Architecture)"
        [[ "$package" == "${expected_package[$key]}" ]] || \
            die "$key contains unexpected package: $package"
        [[ "$version" == "$(guest_lock_value GUEST_KERNEL_PACKAGE_VERSION)" ]] || \
            die "$key contains unexpected version: $version"
        [[ "$architecture" == "${expected_architecture[$key]}" ]] || \
            die "$key contains unexpected architecture: $architecture"
        GUEST_KERNEL_DEB_FILES+=("$destination")
    done
}

prepare_packer_toolchain() {
    local downloads="${LOCAL_ROOT}/downloads"
    local packer_archive="${downloads}/packer_${PACKER_VERSION}_linux_amd64.zip"
    local plugin_archive="${downloads}/packer-plugin-qemu_${PACKER_PLUGIN_VERSION}_linux_amd64.zip"
    local plugin_dir="${PACKER_PLUGIN_ROOT}/github.com/hashicorp/qemu"
    local version_output
    local -a plugin_binaries=()

    require_command curl
    require_command unzip
    download_verified_file "Packer ${PACKER_VERSION}" \
        "$PACKER_URL" "$PACKER_SHA256" "$packer_archive"
    download_verified_file "Packer QEMU plugin ${PACKER_PLUGIN_VERSION}" \
        "$PACKER_PLUGIN_URL" "$PACKER_PLUGIN_SHA256" "$plugin_archive"

    if [[ ! -x "$PACKER_BIN" ]]; then
        mkdir -p "$PACKER_ROOT"
        unzip -q -o "$packer_archive" -d "$PACKER_ROOT"
        chmod 0755 "$PACKER_BIN"
    fi
    version_output="$("$PACKER_BIN" version)"
    grep -F "Packer v${PACKER_VERSION}" <<< "$version_output" >/dev/null || \
        die "installed Packer is not version ${PACKER_VERSION}"

    mkdir -p "$plugin_dir"
    mapfile -t plugin_binaries < <(
        find "$plugin_dir" -maxdepth 1 -type f \
            -name "packer-plugin-qemu_v${PACKER_PLUGIN_VERSION}_x*_linux_amd64" -print
    )
    if [[ "${#plugin_binaries[@]}" -eq 0 ]]; then
        unzip -q -o "$plugin_archive" -d "$plugin_dir"
        mapfile -t plugin_binaries < <(
            find "$plugin_dir" -maxdepth 1 -type f \
                -name "packer-plugin-qemu_v${PACKER_PLUGIN_VERSION}_x*_linux_amd64" -print
        )
    fi
    [[ "${#plugin_binaries[@]}" -eq 1 ]] || \
        die "expected exactly one Packer QEMU ${PACKER_PLUGIN_VERSION} plugin binary"
    chmod 0755 "${plugin_binaries[0]}"
    mkdir -p "$PACKER_CACHE_DIR" "$PACKER_CONFIG_ROOT"
    PACKER_PLUGIN_PATH="$PACKER_PLUGIN_ROOT" \
        PACKER_CONFIG_DIR="$PACKER_CONFIG_ROOT" \
        CHECKPOINT_DISABLE=1 \
        "$PACKER_BIN" plugins install --force --path "${plugin_binaries[0]}" \
        github.com/hashicorp/qemu >/dev/null
    PACKER_PLUGIN_PATH="$PACKER_PLUGIN_ROOT" \
        PACKER_CONFIG_DIR="$PACKER_CONFIG_ROOT" \
        CHECKPOINT_DISABLE=1 \
        "$PACKER_BIN" plugins installed | \
        grep -F "packer-plugin-qemu_v${PACKER_PLUGIN_VERSION}_x5.0_linux_amd64" \
        >/dev/null || die "Packer does not discover the pinned QEMU plugin"
}

directory_fingerprint() {
    local root="$1"
    [[ -d "$root" ]] || return 1
    (
        cd "$root"
        find . -type f -printf 'file\t%m\t%p\0' | LC_ALL=C sort -z
        find . -type l -printf 'symlink\t%m\t%p\t%l\0' | LC_ALL=C sort -z
        find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum --
    ) | sha256sum | awk '{print $1}'
}

array_fingerprint() {
    printf '%s\0' "$@" | sha256sum | awk '{print $1}'
}

qemu_config_has() {
    local setting="$1"
    local config_file
    while IFS= read -r -d '' config_file; do
        grep -qxF "$setting" "$config_file" && return 0
    done < <(find "$QEMU_BUILD_DIR" -type f -name '*-config-devices.mak' -print0 2>/dev/null)
    return 1
}

qemu_feature_fail() {
    local mode="$1"
    shift
    [[ "$mode" == "quiet" ]] || echo "QEMU feature check failed: $*" >&2
    return 1
}

qemu_image_format_available() {
    local format="$1"
    local scratch image info result=0

    scratch="$(mktemp -d "${TMPDIR:-/tmp}/cosim-qemu-img.XXXXXX")" || return 1
    image="${scratch}/probe-${format}.img"
    if ! "$QEMU_IMG" create -q -f "$format" "$image" 1M >/dev/null 2>&1; then
        result=1
    elif ! info="$("$QEMU_IMG" info --output=json "$image" 2>/dev/null)"; then
        result=1
    elif ! grep -Eq "\"format\"[[:space:]]*:[[:space:]]*\"${format}\"" <<<"$info"; then
        result=1
    fi

    rm -f -- "$image"
    rmdir -- "$scratch" 2>/dev/null || true
    return "$result"
}

verify_qemu_features() {
    local mode="${1:-verbose}"
    local output ldd_output

    [[ -x "$QEMU_BIN" ]] || qemu_feature_fail "$mode" "missing executable $QEMU_BIN" || return 1
    [[ -x "$QEMU_IMG" ]] || qemu_feature_fail "$mode" "missing executable $QEMU_IMG" || return 1
    "$QEMU_BIN" --version 2>/dev/null | grep -F "QEMU emulator version ${QEMU_VERSION}" >/dev/null || \
        qemu_feature_fail "$mode" "version is not ${QEMU_VERSION}" || return 1

    output="$("$QEMU_BIN" -device help 2>&1 || true)"
    for device in vfio-user-pci virtio-net-pci virtio-blk-pci virtio-9p-pci; do
        grep -F "\"${device}\"" <<<"$output" >/dev/null || \
            qemu_feature_fail "$mode" "device ${device} is unavailable" || return 1
    done

    output="$("$QEMU_BIN" -machine help 2>&1 || true)"
    grep -E '(^|[[:space:]])q35([[:space:]]|$)' <<<"$output" >/dev/null || \
        qemu_feature_fail "$mode" "q35 machine is unavailable" || return 1

    output="$("$QEMU_BIN" -accel help 2>&1 || true)"
    grep -E '^[[:space:]]*kvm[[:space:]]*$' <<<"$output" >/dev/null || \
        qemu_feature_fail "$mode" "KVM accelerator is unavailable" || return 1

    output="$("$QEMU_BIN" -netdev help 2>&1 || true)"
    grep -E '^[[:space:]]*user[[:space:]]*$' <<<"$output" >/dev/null || \
        qemu_feature_fail "$mode" "user-mode network backend is unavailable" || return 1

    "$QEMU_IMG" --version 2>/dev/null | grep -F "qemu-img version ${QEMU_VERSION}" >/dev/null || \
        qemu_feature_fail "$mode" "qemu-img version is not ${QEMU_VERSION}" || return 1
    qemu_image_format_available raw || \
        qemu_feature_fail "$mode" "qemu-img raw format is unavailable" || return 1
    qemu_image_format_available qcow2 || \
        qemu_feature_fail "$mode" "qemu-img qcow2 format is unavailable" || return 1

    qemu_config_has 'CONFIG_VFIO_PCI=y' || \
        qemu_feature_fail "$mode" "CONFIG_VFIO_PCI=y is absent" || return 1
    qemu_config_has 'CONFIG_VFIO_USER=y' || \
        qemu_feature_fail "$mode" "CONFIG_VFIO_USER=y is absent" || return 1

    ldd_output="$(ldd "$QEMU_BIN" 2>&1 || true)"
    [[ -n "$ldd_output" ]] || qemu_feature_fail "$mode" "ldd produced no result" || return 1
    ! grep -F 'not found' <<<"$ldd_output" >/dev/null || \
        qemu_feature_fail "$mode" "runtime shared library is missing" || return 1
}

verify_qemu_metadata() {
    local archive_sha="$1"
    local source_tree_fingerprint="$2"
    local configure_fingerprint="$3"
    local build_fingerprint="$4"
    local binary_sha qemu_img_sha

    verify_qemu_features quiet || return 1
    [[ "$(metadata_value "$QEMU_META" version || true)" == "$QEMU_VERSION" ]] || return 1
    [[ "$(metadata_value "$QEMU_META" source_sha256 || true)" == "$archive_sha" ]] || return 1
    [[ "$(metadata_value "$QEMU_META" source_fingerprint || true)" == "$source_tree_fingerprint" ]] || return 1
    [[ "$(metadata_value "$QEMU_META" configure_fingerprint || true)" == "$configure_fingerprint" ]] || return 1
    [[ "$(metadata_value "$QEMU_META" build_fingerprint || true)" == "$build_fingerprint" ]] || return 1
    [[ "$(metadata_value "$QEMU_META" signing_verified || true)" == "true" ]] || return 1

    binary_sha="$(sha256sum "$QEMU_BIN" | awk '{print $1}')"
    qemu_img_sha="$(sha256sum "$QEMU_IMG" | awk '{print $1}')"
    [[ "$(metadata_value "$QEMU_META" binary_sha256 || true)" == "$binary_sha" ]] || return 1
    [[ "$(metadata_value "$QEMU_META" qemu_img_sha256 || true)" == "$qemu_img_sha" ]] || return 1
}

qemu_needs_configure() {
    local build_fingerprint="$1"
    [[ ! -f "${QEMU_BUILD_DIR}/build.ninja" ]] && return 0
    [[ "$FORCE" -eq 1 ]] && return 0
    [[ "$(metadata_value "$QEMU_META" build_fingerprint || true)" != "$build_fingerprint" ]]
}

gem5_metadata_matches() {
    local commit="$1"
    local fingerprint="$2"
    local docker_build_recipe_fingerprint="$3"
    local binary_sha docker_image_id

    [[ -x "$GEM5_BIN" ]] || return 1
    [[ "$(metadata_value "$GEM5_META" commit || true)" == "$commit" ]] || return 1
    [[ "$(metadata_value "$GEM5_META" source_fingerprint_algorithm || true)" == \
        "$SOURCE_FINGERPRINT_ALGORITHM" ]] || return 1
    [[ "$(metadata_value "$GEM5_META" source_fingerprint || true)" == "$fingerprint" ]] || \
        return 1
    [[ "$(metadata_value "$GEM5_META" docker_build_recipe_fingerprint || true)" == \
        "$docker_build_recipe_fingerprint" ]] || return 1
    binary_sha="$(sha256sum "$GEM5_BIN" | awk '{print $1}')" || return 1
    [[ "$(metadata_value "$GEM5_META" binary_sha256 || true)" == "$binary_sha" ]] || \
        return 1
    docker_image_id="$(docker image inspect -f '{{.Id}}' "$GEM5_IMAGE" 2>/dev/null)" || \
        return 1
    [[ "$(metadata_value "$GEM5_META" docker_image || true)" == "$docker_image_id" ]]
}

prepare_qemu_source() {
    local allow_unlocked_sha="${1:-false}"
    local extract_source="${2:-true}"
    local downloads="${LOCAL_ROOT}/downloads"
    local key_dir="${LOCAL_ROOT}/keys/qemu-release"
    local gnupg_home="${key_dir}/gnupg"
    local tarball="${downloads}/qemu-${QEMU_VERSION}.tar.xz"
    local signature="${tarball}.sig"
    local key_file="${key_dir}/release-key.asc"

    require_command curl
    require_command gpg
    require_command tar
    require_command sha256sum

    mkdir -p "$downloads" "$key_dir" "$gnupg_home" "${LOCAL_ROOT}/src"
    chmod 700 "$key_dir" "$gnupg_home"

    if [[ ! -f "$tarball" ]]; then
        curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
            --output "${tarball}.part" "$QEMU_URL"
        mv "${tarball}.part" "$tarball"
    fi
    if [[ ! -f "$signature" ]]; then
        curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
            --output "${signature}.part" "$QEMU_SIG_URL"
        mv "${signature}.part" "$signature"
    fi
    if [[ ! -f "$key_file" ]]; then
        curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
            --output "${key_file}.part" "$QEMU_KEY_URL"
        mv "${key_file}.part" "$key_file"
    fi

    local archive_sha key_listing observed_fingerprint primary_key_count
    archive_sha="$(sha256sum "$tarball" | awk '{print $1}')"
    if [[ "$allow_unlocked_sha" != "true" ]]; then
        [[ "$archive_sha" == "$QEMU_SOURCE_SHA256" ]] || die \
            "QEMU archive SHA-256 mismatch: expected $QEMU_SOURCE_SHA256, observed $archive_sha"
    fi

    key_listing="$(gpg --batch --with-colons --import-options show-only \
        --import "$key_file" 2>/dev/null)"
    primary_key_count="$(awk -F: '$1 == "pub" {count++} END {print count + 0}' <<<"$key_listing")"
    [[ "$primary_key_count" == "1" ]] || die \
        "QEMU release-key file must contain exactly one primary key"
    observed_fingerprint="$(awk -F: '$1 == "fpr" {print $10; exit}' <<<"$key_listing")"
    [[ "$observed_fingerprint" == "$QEMU_RELEASE_KEY" ]] || \
        die "QEMU signing-key fingerprint mismatch: ${observed_fingerprint:-missing}"

    gpg --batch --homedir "$gnupg_home" --import "$key_file" >/dev/null 2>&1

    local signature_status
    local -a valid_signers=()
    if ! signature_status="$(gpg --batch --homedir "$gnupg_home" --status-fd 1 \
        --verify "$signature" "$tarball" 2>"${key_dir}/verify.log")"; then
        die "QEMU detached-signature verification failed; see ${key_dir}/verify.log"
    fi
    printf '%s\n' "$signature_status" > "${key_dir}/verify.status"
    mapfile -t valid_signers < <(
        awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" {
            if (NF >= 12 && $12 != "") print $12; else print $3
        }' <<<"$signature_status"
    )
    [[ "${#valid_signers[@]}" -eq 1 ]] || die \
        "QEMU signature must yield exactly one VALIDSIG record"
    [[ "${valid_signers[0]}" == "$QEMU_RELEASE_KEY" ]] || die \
        "QEMU signature primary signer mismatch: ${valid_signers[0]:-missing}"

    if [[ "$extract_source" != "true" ]]; then
        printf '%s\n' "$archive_sha"
        return 0
    fi

    if [[ ! -d "$QEMU_SOURCE_DIR" ]]; then
        local extract_dir
        extract_dir="$(mktemp -d "${LOCAL_ROOT}/src/.qemu-extract.XXXXXX")"
        tar -C "$extract_dir" -xf "$tarball"
        [[ -f "${extract_dir}/qemu-${QEMU_VERSION}/configure" ]] || \
            die "QEMU archive did not contain the expected source tree"
        mv "${extract_dir}/qemu-${QEMU_VERSION}" "$QEMU_SOURCE_DIR"
        rmdir "$extract_dir"
        write_metadata "$QEMU_SOURCE_PROVENANCE" \
            "archive_sha256=${archive_sha}" \
            "fingerprint_algorithm=2" \
            "initial_source_fingerprint=$(directory_fingerprint "$QEMU_SOURCE_DIR")"
    else
        [[ -f "$QEMU_SOURCE_PROVENANCE" ]] || die \
            "existing QEMU source lacks extraction provenance: $QEMU_SOURCE_DIR"
        [[ "$(metadata_value "$QEMU_SOURCE_PROVENANCE" archive_sha256 || true)" == "$archive_sha" ]] || die \
            "existing QEMU source was not extracted from the locked archive"
        if [[ "$(metadata_value "$QEMU_SOURCE_PROVENANCE" fingerprint_algorithm || true)" != "2" ]]; then
            local verify_dir verified_source verified_fingerprint current_fingerprint
            verify_dir="$(mktemp -d "${LOCAL_ROOT}/src/.qemu-verify.XXXXXX")"
            tar -C "$verify_dir" -xf "$tarball"
            verified_source="${verify_dir}/qemu-${QEMU_VERSION}"
            verified_fingerprint="$(directory_fingerprint "$verified_source")"
            current_fingerprint="$(directory_fingerprint "$QEMU_SOURCE_DIR")"
            if [[ "$verified_fingerprint" != "$current_fingerprint" ]]; then
                echo "preserving verification tree after source mismatch: $verify_dir" >&2
                die "existing QEMU source differs from the signed archive"
            fi
            rm -rf -- "$verify_dir"
            write_metadata "$QEMU_SOURCE_PROVENANCE" \
                "archive_sha256=${archive_sha}" \
                "fingerprint_algorithm=2" \
                "initial_source_fingerprint=${verified_fingerprint}"
        fi
    fi

    printf '%s\n' "$archive_sha"
}

lock_qemu_source() {
    validate_qemu_lock true
    if [[ -n "$QEMU_SOURCE_SHA256" ]]; then
        echo "QEMU source lock is already populated: ${QEMU_SOURCE_SHA256}"
        return 0
    fi

    local verified_sha
    verified_sha="$(prepare_qemu_source true false)"
    echo "QEMU detached signature verified against ${QEMU_RELEASE_KEY}."
    echo "QEMU_SOURCE_SHA256=${verified_sha}"
    echo "Record this exact value in ${TOOLCHAIN_LOCK}, then run the qemu action."
}

build_qemu() {
    validate_qemu_lock

    local archive_sha
    archive_sha="$(prepare_qemu_source)"

    local configure_args=(
        "--prefix=${QEMU_PREFIX}"
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
    local initial_source_fingerprint source_tree_fingerprint source_pristine
    local configure_fingerprint build_fingerprint
    initial_source_fingerprint="$(metadata_value \
        "$QEMU_SOURCE_PROVENANCE" initial_source_fingerprint)"
    source_tree_fingerprint="$(directory_fingerprint "$QEMU_SOURCE_DIR")"
    if [[ "$source_tree_fingerprint" == "$initial_source_fingerprint" ]]; then
        source_pristine=true
    else
        source_pristine=false
    fi
    configure_fingerprint="$(array_fingerprint "${configure_args[@]}")"
    build_fingerprint="$(array_fingerprint \
        "$archive_sha" "$source_tree_fingerprint" "$configure_fingerprint")"

    if [[ "$FORCE" -eq 0 ]] && verify_qemu_metadata \
        "$archive_sha" "$source_tree_fingerprint" \
        "$configure_fingerprint" "$build_fingerprint"; then
        echo "QEMU up to date (${QEMU_VERSION}), skipping build"
        return 0
    fi

    require_command make
    require_command ninja
    require_command pkg-config
    require_command python3
    require_command cc
    require_command ldd
    mkdir -p "$QEMU_BUILD_DIR" "$QEMU_PREFIX"

    if qemu_needs_configure "$build_fingerprint"; then
        (
            cd "$QEMU_BUILD_DIR"
            PYTHONDONTWRITEBYTECODE=1 \
                "$QEMU_SOURCE_DIR/configure" "${configure_args[@]}"
        ) 2>&1 | tee "${QEMU_BUILD_DIR}/configure.log"
    fi

    PYTHONDONTWRITEBYTECODE=1 make -C "$QEMU_BUILD_DIR" -j"$QEMU_BUILD_JOBS" \
        2>&1 | tee "${QEMU_BUILD_DIR}/build.log"
    PYTHONDONTWRITEBYTECODE=1 make -C "$QEMU_BUILD_DIR" install \
        2>&1 | tee "${QEMU_BUILD_DIR}/install.log"

    verify_qemu_features verbose || die "QEMU feature verification failed"

    local configure_args_display=""
    local arg quoted_arg
    for arg in "${configure_args[@]}"; do
        printf -v quoted_arg '%q' "$arg"
        configure_args_display+="${quoted_arg} "
    done
    configure_args_display="${configure_args_display% }"

    write_metadata "$QEMU_META" \
        "version=${QEMU_VERSION}" \
        "source_url=${QEMU_URL}" \
        "source_sha256=${archive_sha}" \
        "signature_url=${QEMU_SIG_URL}" \
        "signing_key=${QEMU_RELEASE_KEY}" \
        "signing_verified=true" \
        "initial_source_fingerprint=${initial_source_fingerprint}" \
        "source_fingerprint=${source_tree_fingerprint}" \
        "source_pristine=${source_pristine}" \
        "configure_fingerprint=${configure_fingerprint}" \
        "build_fingerprint=${build_fingerprint}" \
        "configure_args=${configure_args_display}" \
        "binary=${QEMU_BIN}" \
        "binary_sha256=$(sha256sum "$QEMU_BIN" | awk '{print $1}')" \
        "qemu_img=${QEMU_IMG}" \
        "qemu_img_sha256=$(sha256sum "$QEMU_IMG" | awk '{print $1}')" \
        "compiler=$(cc --version | head -n 1)" \
        "timestamp=$(date -Iseconds)"
}

build_gem5() {
    [[ -d "${GEM5_DIR}/.git" || -f "${GEM5_DIR}/.git" ]] || \
        die "gem5 submodule is not initialized: ${GEM5_DIR}"
    require_command docker
    docker info >/dev/null 2>&1 || die "Docker daemon is not available"

    local commit fingerprint docker_build_recipe_fingerprint
    commit="$(git -C "$GEM5_DIR" rev-parse HEAD)"
    fingerprint="$(source_fingerprint "$GEM5_DIR")"
    docker_build_recipe_fingerprint="$(sha256sum < "$GEM5_DOCKERFILE" | awk '{print $1}')"

    if [[ "$FORCE" -eq 0 ]] && gem5_metadata_matches \
        "$commit" "$fingerprint" "$docker_build_recipe_fingerprint"; then
        echo "gem5 up to date (commit ${commit}), skipping build"
        return 0
    fi

    docker build \
        --tag "$GEM5_IMAGE" \
        --tag "$GEM5_RUN_IMAGE" \
        --file "$GEM5_DOCKERFILE" \
        "$SCRIPT_DIR" 2>&1 | tee "${LOCAL_ROOT}/gem5-docker-build.log"

    docker run --rm \
        --user "$(id -u):$(id -g)" \
        --volume "${GEM5_DIR}:/gem5" \
        --workdir /gem5 \
        --env PYTHONPATH=/usr/lib/python3.12/lib-dynload \
        "$GEM5_IMAGE" \
        scons build/VEGA_X86/gem5.opt -j"$GEM5_BUILD_JOBS" \
        2>&1 | tee "${LOCAL_ROOT}/gem5-build.log"

    [[ -x "$GEM5_BIN" ]] || die "gem5 build did not produce ${GEM5_BIN}"
    write_metadata "$GEM5_META" \
        "commit=${commit}" \
        "source_fingerprint_algorithm=${SOURCE_FINGERPRINT_ALGORITHM}" \
        "source_fingerprint=${fingerprint}" \
        "docker_build_recipe_fingerprint=${docker_build_recipe_fingerprint}" \
        "timestamp=$(date -Iseconds)" \
        "target=VEGA_X86" \
        "binary=${GEM5_BIN}" \
        "binary_sha256=$(sha256sum "$GEM5_BIN" | awk '{print $1}')" \
        "docker_image=$(docker image inspect -f '{{.Id}}' "$GEM5_IMAGE")"
}

validate_m5_binary() {
    local binary="$1"
    local file_output elf_header program_headers owner_uid

    [[ -f "$binary" && -x "$binary" && ! -L "$binary" ]] || return 1
    file_output="$(LC_ALL=C file -b "$binary")" || return 1
    [[ "$file_output" == *"ELF 64-bit"* ]] || return 1
    [[ "$file_output" == *"x86-64"* ]] || return 1
    [[ "$file_output" == *"statically linked"* ]] || return 1

    elf_header="$(LC_ALL=C readelf -h "$binary")" || return 1
    grep -Eq 'Class:[[:space:]]+ELF64' <<< "$elf_header" || return 1
    grep -Eq 'Type:[[:space:]]+EXEC' <<< "$elf_header" || return 1
    grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' \
        <<< "$elf_header" || return 1
    program_headers="$(LC_ALL=C readelf -l "$binary")" || return 1
    ! grep -F 'Requesting program interpreter' <<< "$program_headers" >/dev/null || \
        return 1

    owner_uid="$(stat -c '%u' "$binary")" || return 1
    [[ "$owner_uid" == "$(id -u)" ]]
}

m5_core_metadata_matches() {
    local commit="$1"
    local source_fingerprint="$2"
    local dockerfile_sha="$3"
    local docker_image_id="$4"
    local recipe_fingerprint="$5"

    [[ "$(metadata_value "$M5_META" component || true)" == "m5" ]] || return 1
    [[ "$(metadata_value "$M5_META" schema || true)" == "1" ]] || return 1
    [[ "$(metadata_value "$M5_META" gem5_commit || true)" == "$commit" ]] || return 1
    [[ "$(metadata_value "$M5_META" source_fingerprint_algorithm || true)" == \
        "$SOURCE_FINGERPRINT_ALGORITHM" ]] || return 1
    [[ "$(metadata_value "$M5_META" source_fingerprint || true)" == \
        "$source_fingerprint" ]] || return 1
    [[ "$(metadata_value "$M5_META" dockerfile_sha256 || true)" == \
        "$dockerfile_sha" ]] || return 1
    [[ "$(metadata_value "$M5_META" docker_image_id || true)" == \
        "$docker_image_id" ]] || return 1
    [[ "$(metadata_value "$M5_META" recipe_fingerprint || true)" == \
        "$recipe_fingerprint" ]]
}

m5_build_binary_matches() {
    local expected_sha

    validate_m5_binary "$M5_BIN" || return 1
    expected_sha="$(metadata_value "$M5_META" build_binary_sha256 || true)"
    [[ -n "$expected_sha" ]] || return 1
    [[ "$(sha256sum "$M5_BIN" | awk '{print $1}')" == "$expected_sha" ]]
}

m5_guest_file_matches() {
    local expected_sha

    validate_m5_binary "$M5_GUEST_FILE" || return 1
    [[ "$(stat -c '%a' "$M5_GUEST_FILE")" == "755" ]] || return 1
    expected_sha="$(metadata_value "$M5_META" guest_file_sha256 || true)"
    [[ -n "$expected_sha" ]] || return 1
    [[ "$(sha256sum "$M5_GUEST_FILE" | awk '{print $1}')" == "$expected_sha" ]] || \
        return 1
    cmp -s "$M5_BIN" "$M5_GUEST_FILE"
}

stage_m5_guest_file() (
    set -euo pipefail
    local stage_tmp

    mkdir -p "$(dirname "$M5_GUEST_FILE")"
    stage_tmp="$(mktemp "${M5_GUEST_FILE}.tmp.XXXXXX")"
    trap 'rm -f -- "$stage_tmp"' EXIT
    install -m 0755 "$M5_BIN" "$stage_tmp"
    validate_m5_binary "$stage_tmp"
    cmp -s "$M5_BIN" "$stage_tmp"
    mv -- "$stage_tmp" "$M5_GUEST_FILE"
)

write_m5_metadata() {
    local commit="$1"
    local source_fingerprint="$2"
    local dockerfile_sha="$3"
    local docker_image_id="$4"
    local docker_platform="$5"
    local recipe_fingerprint="$6"
    local binary_sha guest_sha

    binary_sha="$(sha256sum "$M5_BIN" | awk '{print $1}')"
    guest_sha="$(sha256sum "$M5_GUEST_FILE" | awk '{print $1}')"
    [[ "$binary_sha" == "$guest_sha" ]] || die "staged m5 does not match build output"

    write_metadata "$M5_META" \
        "component=m5" \
        "schema=1" \
        "gem5_commit=${commit}" \
        "source_fingerprint_algorithm=${SOURCE_FINGERPRINT_ALGORITHM}" \
        "source_fingerprint=${source_fingerprint}" \
        "dockerfile_sha256=${dockerfile_sha}" \
        "docker_image_ref=${GEM5_IMAGE}" \
        "docker_image_id=${docker_image_id}" \
        "docker_platform=${docker_platform}" \
        "recipe_fingerprint=${recipe_fingerprint}" \
        "abi=x86" \
        "scons_target=build/x86/out/m5" \
        "build_binary=${M5_BIN}" \
        "build_binary_sha256=${binary_sha}" \
        "build_binary_size=$(stat -c '%s' "$M5_BIN")" \
        "build_binary_mode=$(stat -c '%a' "$M5_BIN")" \
        "guest_file=${M5_GUEST_FILE}" \
        "guest_file_sha256=${guest_sha}" \
        "guest_file_size=$(stat -c '%s' "$M5_GUEST_FILE")" \
        "guest_file_mode=$(stat -c '%a' "$M5_GUEST_FILE")" \
        "guest_install=/sbin/m5" \
        "timestamp=$(date -Iseconds)"
}

build_m5() {
    [[ -d "${RESOURCES_DIR}/.git" || -f "${RESOURCES_DIR}/.git" ]] || \
        die "gem5-resources submodule is not initialized: ${RESOURCES_DIR}"
    require_command docker
    require_command file
    require_command readelf
    require_command install
    require_command cmp

    # This also creates or validates the exact pinned build image.
    build_gem5

    local commit source_fingerprint dockerfile_sha docker_image_id docker_platform
    local commit_epoch recipe_fingerprint core_matches=0 recovery_path
    commit="$(git -C "$GEM5_DIR" rev-parse HEAD)"
    source_fingerprint="$(source_fingerprint "$GEM5_DIR")"
    dockerfile_sha="$(sha256sum < "$GEM5_DOCKERFILE" | awk '{print $1}')"
    docker_image_id="$(docker image inspect -f '{{.Id}}' "$GEM5_IMAGE")"
    docker_platform="$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$GEM5_IMAGE")"
    [[ "$docker_platform" == "linux/amd64" ]] || \
        die "m5 build image platform is ${docker_platform}, expected linux/amd64"
    commit_epoch="$(git -C "$GEM5_DIR" show -s --format=%ct "$commit")"
    recipe_fingerprint="$(array_fingerprint \
        "m5-recipe-v${M5_RECIPE_VERSION}" \
        "platform=linux/amd64" \
        "workdir=/gem5/util/m5" \
        "abi=x86" \
        "cross_compile=" \
        "target=build/x86/out/m5" \
        "LC_ALL=C" "LANG=C" "TZ=UTC" \
        "SOURCE_DATE_EPOCH=${commit_epoch}" \
        "dockerfile=${dockerfile_sha}" \
        "image=${docker_image_id}")"

    if m5_core_metadata_matches "$commit" "$source_fingerprint" \
        "$dockerfile_sha" "$docker_image_id" "$recipe_fingerprint"; then
        core_matches=1
    fi

    if [[ "$FORCE" -eq 0 && "$core_matches" -eq 1 ]] && m5_build_binary_matches; then
        if m5_guest_file_matches; then
            echo "m5 up to date (commit ${commit}), skipping build"
            return 0
        fi
        stage_m5_guest_file
        write_m5_metadata "$commit" "$source_fingerprint" "$dockerfile_sha" \
            "$docker_image_id" "$docker_platform" "$recipe_fingerprint"
        echo "m5 build output was valid; repaired guest staging copy"
        return 0
    fi

    # Never let SCons bless an unproven or tampered final binary merely because
    # its timestamp looks current. Preserve just that generated target and let
    # the incremental graph regenerate it; the rest of the build tree remains.
    if [[ -e "$M5_BIN" ]]; then
        recovery_path="${M5_BIN}.pre-rebuild.$(date +%Y%m%dT%H%M%S)"
        mv -- "$M5_BIN" "$recovery_path"
        echo "preserved previous m5 build output: $recovery_path"
    fi

    docker run --rm \
        --platform linux/amd64 \
        --user "$(id -u):$(id -g)" \
        --volume "${GEM5_DIR}:/gem5" \
        --workdir /gem5/util/m5 \
        --env LC_ALL=C \
        --env LANG=C \
        --env TZ=UTC \
        --env "SOURCE_DATE_EPOCH=${commit_epoch}" \
        "$GEM5_IMAGE" \
        scons x86.CROSS_COMPILE= build/x86/out/m5 -j"$M5_BUILD_JOBS" \
        2>&1 | tee "${LOCAL_ROOT}/m5-build.log"

    validate_m5_binary "$M5_BIN" || die "m5 build output failed static validation"
    stage_m5_guest_file
    write_m5_metadata "$commit" "$source_fingerprint" "$dockerfile_sha" \
        "$docker_image_id" "$docker_platform" "$recipe_fingerprint"
    echo "m5 built and staged for the guest image: ${M5_GUEST_FILE}"
}

validate_guest_image() {
    local image="$1"
    local info

    [[ -f "$image" ]] || return 1
    info="$("$QEMU_IMG" info --output=json "$image")" || return 1
    jq -e '
        .format == "raw" and
        .["virtual-size"] >= (50 * 1024 * 1024 * 1024)
    ' <<< "$info" >/dev/null
}

validate_guest_kernel() {
    local kernel="$1"
    local header

    [[ -f "$kernel" && -s "$kernel" ]] || return 1
    header="$(LC_ALL=C readelf -h "$kernel")" || return 1
    grep -Eq 'Class:[[:space:]]+ELF64' <<< "$header" || return 1
    grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' \
        <<< "$header" || return 1
    grep -a -F -m 1 'Linux version 6.8.0-79-generic' "$kernel" >/dev/null
}

guest_metadata_matches() {
    local recipe_fingerprint="$1"
    local image_sha kernel_sha

    [[ "$(metadata_value "$GUEST_META" component || true)" == "guest" ]] || return 1
    [[ "$(metadata_value "$GUEST_META" schema || true)" == "1" ]] || return 1
    [[ "$(metadata_value "$GUEST_META" recipe_fingerprint || true)" == \
        "$recipe_fingerprint" ]] || return 1
    validate_guest_image "$GUEST_IMAGE" || return 1
    validate_guest_kernel "$GUEST_KERNEL_IMAGE" || return 1
    image_sha="$(sha256sum "$GUEST_IMAGE" | awk '{print $1}')" || return 1
    kernel_sha="$(sha256sum "$GUEST_KERNEL_IMAGE" | awk '{print $1}')" || return 1
    [[ "$(metadata_value "$GUEST_META" image_sha256 || true)" == "$image_sha" ]] || \
        return 1
    [[ "$(metadata_value "$GUEST_META" kernel_sha256 || true)" == "$kernel_sha" ]]
}

stage_guest_outputs() {
    local built_image="$1"
    local built_kernel="$2"
    local attempt_id="$3"
    local backup_dir="${GUEST_BUILD_ROOT}/backups/${attempt_id}"

    mkdir -p "$(dirname "$GUEST_IMAGE")" "$backup_dir"
    if [[ -e "$GUEST_IMAGE" ]]; then
        mv -- "$GUEST_IMAGE" "$backup_dir/$(basename "$GUEST_IMAGE")"
    fi
    if [[ -e "$GUEST_KERNEL_IMAGE" ]]; then
        mv -- "$GUEST_KERNEL_IMAGE" "$backup_dir/$(basename "$GUEST_KERNEL_IMAGE")"
    fi
    mv -- "$built_image" "$GUEST_IMAGE"
    mv -- "$built_kernel" "$GUEST_KERNEL_IMAGE"
}

build_guest() {
    [[ -d "${RESOURCES_DIR}/.git" || -f "${RESOURCES_DIR}/.git" ]] || \
        die "gem5-resources submodule is not initialized: ${RESOURCES_DIR}"
    require_command patch
    require_command tar
    require_command jq
    require_command readelf
    require_command shellcheck
    require_command timeout
    [[ -r /dev/kvm && -w /dev/kvm ]] || \
        die "the current process cannot read/write /dev/kvm"

    validate_guest_lock
    build_qemu
    build_m5
    prepare_packer_toolchain
    prepare_guest_kernel_debs

    local resources_commit template_tree patch_sha m5_sha qemu_sha qemu_img_sha
    local lock_sha recipe_fingerprint attempt_id attempt_root context artifact_dir
    local built_image built_kernel image_sha kernel_sha image_size kernel_size
    local packer_rc tee_rc classification log_name kernel_deb
    local -a pipeline_status
    resources_commit="$(git -C "$RESOURCES_DIR" rev-parse HEAD)"
    template_tree="$(git -C "$RESOURCES_DIR" rev-parse "${resources_commit}:${GUEST_TEMPLATE_REL}")"
    patch_sha="$(sha256sum "$GUEST_PATCH" | awk '{print $1}')"
    m5_sha="$(sha256sum "$M5_GUEST_FILE" | awk '{print $1}')"
    qemu_sha="$(sha256sum "$QEMU_BIN" | awk '{print $1}')"
    qemu_img_sha="$(sha256sum "$QEMU_IMG" | awk '{print $1}')"
    lock_sha="$(sha256sum "$GUEST_LOCK" | awk '{print $1}')"
    recipe_fingerprint="$(array_fingerprint \
        "guest-recipe-v${GUEST_RECIPE_VERSION}" \
        "resources_commit=${resources_commit}" \
        "template_tree=${template_tree}" \
        "overlay_patch=${patch_sha}" \
        "m5=${m5_sha}" \
        "qemu=${qemu_sha}" \
        "qemu_img=${qemu_img_sha}" \
        "packer=${PACKER_SHA256}" \
        "packer_plugin=${PACKER_PLUGIN_SHA256}" \
        "guest_lock=${lock_sha}")"

    if [[ "$FORCE" -eq 0 ]] && guest_metadata_matches "$recipe_fingerprint"; then
        echo "guest image up to date (resources commit ${resources_commit}), skipping build"
        return 0
    fi

    attempt_id="$(date +%Y%m%dT%H%M%S)-$$"
    attempt_root="${GUEST_BUILD_ROOT}/attempts/${attempt_id}"
    context="${attempt_root}/context/${GUEST_TEMPLATE_REL}"
    artifact_dir="${GUEST_ARTIFACT_ROOT}/${attempt_id}"
    mkdir -p "${attempt_root}/context" "$artifact_dir" "$PACKER_CONFIG_ROOT"
    for log_name in installer-serial.log packer.log console.log; do
        install -m 0600 /dev/null "${artifact_dir}/${log_name}"
    done

    git -C "$RESOURCES_DIR" archive "$resources_commit" "$GUEST_TEMPLATE_REL" |
        tar -x -C "${attempt_root}/context"
    patch --directory="${attempt_root}/context" --strip=1 --fuzz=0 --batch \
        --input="$GUEST_PATCH" | tee "${artifact_dir}/overlay.log"
    install -m 0755 "$M5_GUEST_FILE" "${context}/files/m5"
    mkdir -p "${context}/files/kernel-debs"
    for kernel_deb in "${GUEST_KERNEL_DEB_FILES[@]}"; do
        install -m 0644 "$kernel_deb" "${context}/files/kernel-debs/"
    done
    bash -n "${context}/scripts/rocm-install.sh"
    shellcheck "${context}/scripts/rocm-install.sh"
    grep -F "version = \"= ${PACKER_PLUGIN_VERSION}\"" \
        "${context}/x86-ubuntu-gpu-ml.pkr.hcl" >/dev/null || \
        die "guest context did not pin the expected Packer QEMU plugin"
    grep -F "$(guest_lock_value UBUNTU_ISO_URL)" \
        "${context}/x86-ubuntu-gpu-ml.pkr.hcl" >/dev/null || \
        die "guest context did not pin the expected Ubuntu ISO URL"

    echo "guest build attempt: ${attempt_id}"
    echo "guest build evidence: ${artifact_dir}"
    set +e
    (
        local qemu_dir
        cd "$context"
        qemu_dir="$(dirname "$QEMU_BIN")"
        export PATH="${qemu_dir}:${PATH}"
        export PACKER_CACHE_DIR
        export PACKER_PLUGIN_PATH="$PACKER_PLUGIN_ROOT"
        export PACKER_CONFIG_DIR="$PACKER_CONFIG_ROOT"
        export CHECKPOINT_DISABLE=1
        export PACKER_LOG=1
        export PACKER_LOG_PATH="${artifact_dir}/packer.log"
        "$PACKER_BIN" init x86-ubuntu-gpu-ml.pkr.hcl
        "$PACKER_BIN" validate -var "qemu_path=${QEMU_BIN}" \
            -var "serial_log=${artifact_dir}/installer-serial.log" \
            x86-ubuntu-gpu-ml.pkr.hcl
        timeout --signal=INT --kill-after=2m --foreground \
            "${COSIM_GUEST_BUILD_TIMEOUT:-4h}" \
            "$PACKER_BIN" build -color=false -var "qemu_path=${QEMU_BIN}" \
            -var "serial_log=${artifact_dir}/installer-serial.log" \
            x86-ubuntu-gpu-ml.pkr.hcl
    ) 2>&1 | tee "${artifact_dir}/console.log"
    pipeline_status=("${PIPESTATUS[@]}")
    packer_rc="${pipeline_status[0]}"
    tee_rc="${pipeline_status[1]}"
    set -e
    if (( packer_rc != 0 || tee_rc != 0 )); then
        if (( tee_rc != 0 )); then
            classification="artifact_write_failure"
        elif (( packer_rc == 124 )); then
            classification="host_timeout"
        elif grep -Fq 'COSIM_AUTOINSTALL_ERROR' \
            "${artifact_dir}/installer-serial.log"; then
            classification="autoinstall_error"
        elif ! grep -Fq 'COSIM_AUTOINSTALL_START' \
            "${artifact_dir}/installer-serial.log"; then
            classification="boot_or_autoinstall_config_failure"
        elif ! grep -Fq 'COSIM_AUTOINSTALL_COMPLETE' \
            "${artifact_dir}/installer-serial.log"; then
            classification="autoinstall_incomplete"
        elif grep -Eq 'Connected to SSH|Provisioning with' \
            "${artifact_dir}/console.log"; then
            classification="packer_provisioner_failure"
        else
            classification="target_boot_or_ssh_failure"
        fi
        write_metadata "${artifact_dir}/attempt-status.txt" \
            "status=failed" \
            "classification=${classification}" \
            "packer_exit_code=${packer_rc}" \
            "tee_exit_code=${tee_rc}" \
            "attempt=${attempt_id}" \
            "timestamp=$(date -Iseconds)"
        echo "guest build failed; evidence and context preserved:" >&2
        echo "  classification: ${classification}" >&2
        echo "  ${artifact_dir}" >&2
        echo "  ${attempt_root}" >&2
        return 1
    fi

    built_image="${context}/disk-image/x86-ubuntu-rocm70"
    built_kernel="${context}/vmlinux-rocm70"
    if ! validate_guest_image "$built_image"; then
        write_metadata "${artifact_dir}/attempt-status.txt" \
            "status=failed" \
            "classification=post_packer_image_validation_failure" \
            "packer_exit_code=0" \
            "tee_exit_code=0" \
            "attempt=${attempt_id}" \
            "timestamp=$(date -Iseconds)"
        echo "guest build failed host-side raw-image validation; evidence preserved:" >&2
        echo "  ${artifact_dir}" >&2
        echo "  ${attempt_root}" >&2
        return 1
    fi
    if ! validate_guest_kernel "$built_kernel"; then
        write_metadata "${artifact_dir}/attempt-status.txt" \
            "status=failed" \
            "classification=post_packer_kernel_validation_failure" \
            "packer_exit_code=0" \
            "tee_exit_code=0" \
            "attempt=${attempt_id}" \
            "timestamp=$(date -Iseconds)"
        echo "guest build failed host-side kernel validation; evidence preserved:" >&2
        echo "  ${artifact_dir}" >&2
        echo "  ${attempt_root}" >&2
        return 1
    fi
    image_sha="$(sha256sum "$built_image" | awk '{print $1}')"
    kernel_sha="$(sha256sum "$built_kernel" | awk '{print $1}')"
    image_size="$(stat -c '%s' "$built_image")"
    kernel_size="$(stat -c '%s' "$built_kernel")"

    stage_guest_outputs "$built_image" "$built_kernel" "$attempt_id"
    write_metadata "$GUEST_META" \
        "component=guest" \
        "schema=1" \
        "resources_commit=${resources_commit}" \
        "template_tree=${template_tree}" \
        "overlay_patch_sha256=${patch_sha}" \
        "guest_lock_sha256=${lock_sha}" \
        "recipe_fingerprint=${recipe_fingerprint}" \
        "packer_version=${PACKER_VERSION}" \
        "packer_sha256=${PACKER_SHA256}" \
        "packer_qemu_plugin_version=${PACKER_PLUGIN_VERSION}" \
        "packer_qemu_plugin_sha256=${PACKER_PLUGIN_SHA256}" \
        "ubuntu_iso_url=$(guest_lock_value UBUNTU_ISO_URL)" \
        "ubuntu_iso_sha256=$(guest_lock_value UBUNTU_ISO_SHA256)" \
        "amdgpu_dkms_version=$(guest_lock_value AMDGPU_DKMS_VERSION)" \
        "rocm_version=$(guest_lock_value ROCM_VERSION)" \
        "kernel_version=$(guest_lock_value GUEST_KERNEL)" \
        "qemu_binary_sha256=${qemu_sha}" \
        "qemu_img_sha256=${qemu_img_sha}" \
        "m5_sha256=${m5_sha}" \
        "image=${GUEST_IMAGE}" \
        "image_sha256=${image_sha}" \
        "image_size=${image_size}" \
        "kernel=${GUEST_KERNEL_IMAGE}" \
        "kernel_sha256=${kernel_sha}" \
        "kernel_size=${kernel_size}" \
        "artifacts=${artifact_dir}" \
        "timestamp=$(date -Iseconds)"
    cp "$GUEST_META" "${artifact_dir}/provenance.txt"
    write_metadata "${artifact_dir}/attempt-status.txt" \
        "status=passed" \
        "classification=guest_build_complete" \
        "packer_exit_code=0" \
        "tee_exit_code=0" \
        "attempt=${attempt_id}" \
        "timestamp=$(date -Iseconds)"
    echo "guest image built: ${GUEST_IMAGE}"
    echo "guest kernel built: ${GUEST_KERNEL_IMAGE}"
}

show_status() {
    local lock_error
    echo "QEMU binary: $QEMU_BIN"
    if ! lock_error="$(validate_qemu_lock 2>&1)"; then
        echo "QEMU status: not ready (${lock_error#ERROR: })"
    elif verify_qemu_features; then
        "$QEMU_BIN" --version | head -n 1
        [[ -f "$QEMU_META" ]] && cat "$QEMU_META"
    else
        echo "QEMU status: not ready"
    fi
    echo ""
    echo "gem5 binary: $GEM5_BIN"
    if [[ -x "$GEM5_BIN" ]]; then
        [[ -f "$GEM5_META" ]] && cat "$GEM5_META"
        sha256sum "$GEM5_BIN"
    else
        echo "gem5 status: not ready"
    fi
    echo ""
    echo "m5 guest file: $M5_GUEST_FILE"
    if [[ -x "$M5_GUEST_FILE" ]]; then
        [[ -f "$M5_META" ]] && cat "$M5_META"
        sha256sum "$M5_GUEST_FILE"
    else
        echo "m5 status: not ready"
    fi
    echo ""
    echo "guest image: $GUEST_IMAGE"
    echo "guest kernel: $GUEST_KERNEL_IMAGE"
    if [[ -f "$GUEST_IMAGE" && -f "$GUEST_KERNEL_IMAGE" ]]; then
        [[ -f "$GUEST_META" ]] && cat "$GUEST_META"
    else
        echo "guest status: not ready"
    fi
    return 0
}

main() {
    [[ $# -ge 1 ]] || { usage; return 2; }
    local action="$1"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --force) FORCE=1; shift ;;
            -h|--help) usage; return 0 ;;
            *) die "unknown argument: $1" ;;
        esac
    done

    require_command flock
    mkdir -p "$LOCAL_ROOT"
    exec 9>"${LOCAL_ROOT}/build.lock"
    flock 9

    case "$action" in
        lock-qemu-source) lock_qemu_source ;;
        qemu) build_qemu ;;
        gem5) build_gem5 ;;
        m5) build_m5 ;;
        guest) build_guest ;;
        all) build_guest ;;
        status) show_status ;;
        *) usage; return 2 ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
