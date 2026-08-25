#!/bin/bash
# Shared library for cosim infrastructure: run-ID generation, resource path
# computation, failure taxonomy, resource manifest, and diagnostic utilities.

# ---- Run-ID Generation ----

generate_run_id() {
    local ts
    ts="$(date +%Y%m%d-%H%M%S)"
    local rand
    rand="$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"
    echo "${ts}-${rand}"
}

# ---- Resource Path Computation ----

cosim_container_name() {
    local run_id="$1"
    echo "gem5-cosim-${run_id}"
}

cosim_socket_path() {
    local run_id="$1"
    local gpu_id="${2:-0}"
    local num_gpus="${3:-1}"
    if [[ "$num_gpus" -eq 1 ]]; then
        echo "/tmp/gem5-mi300x-${run_id}.sock"
    else
        echo "/tmp/gem5-mi300x-${run_id}-${gpu_id}.sock"
    fi
}

cosim_vram_shmem_path() {
    local run_id="$1"
    local gpu_id="${2:-0}"
    local num_gpus="${3:-1}"
    if [[ "$num_gpus" -eq 1 ]]; then
        echo "/dev/shm/mi300x-vram-${run_id}"
    else
        echo "/dev/shm/mi300x-vram-${run_id}-${gpu_id}"
    fi
}

cosim_guest_ram_shmem_path() {
    local run_id="$1"
    echo "/dev/shm/cosim-guest-ram-${run_id}"
}

cosim_session_dir() {
    local run_id="$1"
    local session_name="${2:-cosim}"
    echo "/tmp/${session_name}-${run_id}.session"
}

cosim_screen_log() {
    local run_id="$1"
    local session_name="${2:-cosim}"
    echo "/tmp/${session_name}-${run_id}.log"
}

cosim_artifact_dir() {
    local cosim_dir="$1"
    local operator="$2"
    local run_id="$3"
    echo "${cosim_dir}/artifacts/${operator}/${run_id}"
}

# ---- Failure Taxonomy (exported for use by sourcing scripts) ----

export COSIM_CAT_TEST_PASS="test_pass"
export COSIM_CAT_TEST_FAIL="test_fail"
export COSIM_CAT_TEST_TIMEOUT="test_timeout"
export COSIM_CAT_BOOT_TIMEOUT="boot_timeout"
export COSIM_CAT_GEM5_INIT_TIMEOUT="gem5_init_timeout"
export COSIM_CAT_GEM5_EXIT="gem5_exit"
export COSIM_CAT_QEMU_EXIT="qemu_exit"
export COSIM_CAT_READINESS_FAIL="readiness_fail"
export COSIM_CAT_STALE_CONFLICT="stale_conflict"
export COSIM_CAT_INTERRUPT="interrupt"
export COSIM_CAT_CLEANUP_FAIL="cleanup_fail"
export COSIM_CAT_LAUNCHER_EXIT="launcher_exit"
export COSIM_CAT_INFRA_UNKNOWN="infra_unknown"

is_infra_failure() {
    local category="$1"
    case "$category" in
        "$COSIM_CAT_TEST_PASS"|"$COSIM_CAT_TEST_FAIL"|"$COSIM_CAT_TEST_TIMEOUT"|"$COSIM_CAT_INTERRUPT")
            return 1 ;;
        *)
            return 0 ;;
    esac
}

cosim_recorded_runner_category() {
    local artifact_dir="$1"
    local category_file="${artifact_dir}/runner-category.txt"
    local -a lines=()

    [[ -f "$category_file" && ! -L "$category_file" ]] || return 1
    mapfile -t lines < "$category_file"
    [[ "${#lines[@]}" -eq 1 && -n "${lines[0]}" ]] || return 1
    case "${lines[0]}" in
        "$COSIM_CAT_TEST_PASS"|"$COSIM_CAT_TEST_FAIL"|\
        "$COSIM_CAT_TEST_TIMEOUT"|"$COSIM_CAT_BOOT_TIMEOUT"|\
        "$COSIM_CAT_GEM5_INIT_TIMEOUT"|"$COSIM_CAT_GEM5_EXIT"|\
        "$COSIM_CAT_QEMU_EXIT"|"$COSIM_CAT_READINESS_FAIL"|\
        "$COSIM_CAT_STALE_CONFLICT"|"$COSIM_CAT_INTERRUPT"|\
        "$COSIM_CAT_CLEANUP_FAIL"|"$COSIM_CAT_LAUNCHER_EXIT"|\
        "$COSIM_CAT_INFRA_UNKNOWN")
            printf '%s\n' "${lines[0]}"
            ;;
        *)
            return 1
            ;;
    esac
}

# ---- Resource Manifest ----

COSIM_MANIFEST_FILE=""

manifest_init() {
    local session_dir="$1"
    local run_id="$2"
    local repo_root="$3"
    COSIM_MANIFEST_FILE="${session_dir}/resources.manifest"
    [[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || return 1
    [[ "$run_id" != *..* ]] || return 1
    [[ "$repo_root" == /* && "$repo_root" != *'|'* && "$repo_root" != *$'\n'* ]] || return 1
    mkdir -p "$session_dir"
    [[ ! -e "$COSIM_MANIFEST_FILE" ]] || {
        echo "manifest already exists: $COSIM_MANIFEST_FILE" >&2
        return 1
    }
    printf '%s\n' \
        'schema|cosim-resource-manifest|2' \
        "identity|run_id|${run_id}" \
        "identity|repo_root|${repo_root}" > "$COSIM_MANIFEST_FILE"
}

manifest_add() {
    local role="$1"   # runtime or artifact
    local type="$2"   # container, socket, shmem, file, directory
    local path="$3"
    [[ -n "$COSIM_MANIFEST_FILE" ]] || return 1
    [[ "$role" == "runtime" || "$role" == "artifact" ]] || return 1
    case "$type" in
        container|socket|shmem|file|directory) ;;
        *) return 1 ;;
    esac
    [[ "$path" != *'|'* && "$path" != *$'\n'* && -n "$path" ]] || return 1
    printf '%s|%s|%s\n' "$role" "$type" "$path" >> "$COSIM_MANIFEST_FILE"
}

manifest_runtime_paths() {
    [[ -f "$COSIM_MANIFEST_FILE" ]] || return
    awk -F'|' '$1 == "runtime" && $2 != "container" {print $3}' \
        "$COSIM_MANIFEST_FILE"
}

manifest_artifact_paths() {
    [[ -f "$COSIM_MANIFEST_FILE" ]] || return
    grep '^artifact|' "$COSIM_MANIFEST_FILE" | cut -d'|' -f3
}

# ---- Diagnostic Artifact Capture ----

capture_artifacts() {
    local artifact_dir="$1"
    local container_name="$2"
    local screen_log="${3:-}"
    local run_id="${4:-unknown}"
    local category="${5:-$COSIM_CAT_INFRA_UNKNOWN}"

    mkdir -p "$artifact_dir"

    echo "run_id=${run_id}" > "${artifact_dir}/launcher-metadata.txt"
    echo "category=${category}" >> "${artifact_dir}/launcher-metadata.txt"
    echo "timestamp=$(date -Iseconds)" >> "${artifact_dir}/launcher-metadata.txt"

    if [[ ! -s "${artifact_dir}/gem5.log" ]]; then
        docker logs "$container_name" > "${artifact_dir}/gem5.log" 2>&1 || true
    fi
    docker inspect "$container_name" > "${artifact_dir}/docker-inspect.json" 2>&1 || true

    if [[ -n "$screen_log" && -f "$screen_log" ]]; then
        cp "$screen_log" "${artifact_dir}/qemu-console.log" 2>/dev/null || true
    fi

    ls -la /dev/shm/ > "${artifact_dir}/devshm-listing.txt" 2>&1 || true
    ls -la /tmp/gem5-mi300x*.sock > "${artifact_dir}/socket-listing.txt" 2>&1 || true
    pgrep -af '(gem5|qemu)' > "${artifact_dir}/process-snapshot.txt" 2>&1 || true
    docker ps -a --filter "name=gem5-cosim" > "${artifact_dir}/docker-ps.txt" 2>&1 || true
}

# ---- Cleanup Utilities ----

runtime_path_is_safe() {
    local run_id="$1"
    local type="$2"
    local path="$3"

    [[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || return 1
    [[ "$run_id" != *..* ]] || return 1
    [[ "$path" == /* ]] || return 1
    [[ "$(realpath -m -- "$path")" == "$path" ]] || return 1

    case "$type" in
        socket)
            [[ "$path" == "/tmp/gem5-mi300x-${run_id}.sock" ||
               "$path" == /tmp/gem5-mi300x-"${run_id}"-[0-9]*.sock ]]
            ;;
        shmem)
            [[ "$path" == "/dev/shm/mi300x-vram-${run_id}" ||
               "$path" == /dev/shm/mi300x-vram-"${run_id}"-[0-9]* ||
               "$path" == "/dev/shm/cosim-guest-ram-${run_id}" ]]
            ;;
        directory)
            [[ "$path" == /tmp/*-"${run_id}".session ]]
            ;;
        file)
            [[ "$path" == /tmp/*"${run_id}"* ]]
            ;;
        *)
            return 1
            ;;
    esac
}

cleanup_from_manifest() {
    local container_name="$1"
    local run_id="${container_name#gem5-cosim-}"
    local cleanup_failed=0

    if [[ "$container_name" != "gem5-cosim-${run_id}" ||
          ! "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ||
          "$run_id" == *..* ]]; then
        echo "cleanup refused unsafe container name: $container_name" >&2
        return 1
    fi

    [[ -f "$COSIM_MANIFEST_FILE" && ! -L "$COSIM_MANIFEST_FILE" ]] || {
        echo "cleanup refused missing or symlinked manifest: $COSIM_MANIFEST_FILE" >&2
        return 1
    }

    local expected_repo_root manifest_run_id manifest_repo_root
    expected_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    [[ "$(sed -n '1p' "$COSIM_MANIFEST_FILE")" == \
        'schema|cosim-resource-manifest|2' ]] || {
        echo "cleanup refused unsupported manifest schema" >&2
        return 1
    }
    manifest_run_id="$(awk -F'|' '$1 == "identity" && $2 == "run_id" {print $3}' \
        "$COSIM_MANIFEST_FILE")"
    manifest_repo_root="$(awk -F'|' '$1 == "identity" && $2 == "repo_root" {print $3}' \
        "$COSIM_MANIFEST_FILE")"
    [[ "$manifest_run_id" == "$run_id" && "$manifest_repo_root" == "$expected_repo_root" ]] || {
        echo "cleanup refused mismatched manifest identity" >&2
        return 1
    }
    [[ "$(awk -F'|' -v c="$container_name" \
        '$1 == "runtime" && $2 == "container" && $3 == c {count++} END {print count + 0}' \
        "$COSIM_MANIFEST_FILE")" -eq 1 ]] || {
        echo "cleanup refused manifest without one expected container entry" >&2
        return 1
    }

    # Snapshot typed runtime entries before deleting the session directory.
    local -a runtime_types=()
    local -a runtime_paths=()
    local role type path
    while IFS='|' read -r role type path; do
        [[ "$role" == "runtime" ]] || continue
        [[ "$type" == "container" ]] && continue
        if ! runtime_path_is_safe "$run_id" "$type" "$path"; then
            echo "cleanup refused unsafe manifest entry: $type|$path" >&2
            cleanup_failed=1
            continue
        fi
        runtime_types+=("$type")
        runtime_paths+=("$path")
    done < "$COSIM_MANIFEST_FILE"

    # Store for verify_cleanup
    _COSIM_RUNTIME_PATHS=("${runtime_paths[@]+"${runtime_paths[@]}"}")

    if docker inspect "$container_name" >/dev/null 2>&1; then
        local label_run_id label_repo_root
        label_run_id="$(docker inspect -f '{{ index .Config.Labels "io.cosim-gpu.run-id" }}' \
            "$container_name" 2>/dev/null || true)"
        label_repo_root="$(docker inspect -f '{{ index .Config.Labels "io.cosim-gpu.repo-root" }}' \
            "$container_name" 2>/dev/null || true)"
        if [[ "$label_run_id" != "$run_id" || "$label_repo_root" != "$expected_repo_root" ]]; then
            echo "cleanup refused container with mismatched ownership labels: $container_name" >&2
            cleanup_failed=1
        else
            docker rm -f "$container_name" >/dev/null 2>&1 || cleanup_failed=1
        fi
    fi

    local index
    for index in "${!runtime_paths[@]}"; do
        type="${runtime_types[$index]}"
        path="${runtime_paths[$index]}"
        case "$type" in
            directory)
                if [[ -L "$path" ]]; then
                    echo "cleanup refused symlinked directory: $path" >&2
                    cleanup_failed=1
                elif [[ -e "$path" ]]; then
                    rm -rf -- "$path" 2>/dev/null || cleanup_failed=1
                fi
                ;;
            socket|shmem|file)
                if [[ -d "$path" && ! -L "$path" ]]; then
                    echo "cleanup refused directory in ${type} entry: $path" >&2
                    cleanup_failed=1
                elif [[ -e "$path" || -L "$path" ]]; then
                    rm -f -- "$path" 2>/dev/null || cleanup_failed=1
                fi
                ;;
        esac
    done

    [[ "$cleanup_failed" -eq 0 ]]
}

verify_cleanup() {
    local timeout_secs="${1:-10}"
    local container_name="${2:-}"
    local elapsed=0

    local -a paths=("${_COSIM_RUNTIME_PATHS[@]+"${_COSIM_RUNTIME_PATHS[@]}"}")

    while [[ $elapsed -lt $timeout_secs ]]; do
        local remaining=0

        if [[ -n "$container_name" ]]; then
            if docker inspect "$container_name" >/dev/null 2>&1; then
                remaining=$((remaining + 1))
            fi
        fi

        for path in "${paths[@]+"${paths[@]}"}"; do
            if [[ -e "$path" ]]; then
                remaining=$((remaining + 1))
            fi
        done

        if [[ $remaining -eq 0 ]]; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

_COSIM_RUNTIME_PATHS=()

# ---- Force-Clean (Dry-Run by Default) ----

force_clean_orphans() {
    local confirm="${1:-false}"
    local found=0

    if [[ "$confirm" == "true" ]]; then
        echo "refusing unscoped orphan deletion; use cosim_cleanup.sh with a valid manifest" >&2
        return 1
    fi

    local c
    while IFS= read -r c; do
        [[ -z "$c" ]] && continue
        echo "  orphan container: $c"
        found=1
    done < <(docker ps -a --filter "name=gem5-cosim-" --filter "status=exited" --filter "status=dead" --filter "status=created" --format '{{.Names}}' 2>/dev/null)

    local -a _active_rids=()
    local _cname
    while IFS= read -r _cname; do
        [[ -z "$_cname" ]] && continue
        _active_rids+=("${_cname#gem5-cosim-}")
    done < <(docker ps --filter "name=gem5-cosim-" --format '{{.Names}}' 2>/dev/null)

    _resource_is_active() {
        local name="$1"
        local rid
        for rid in "${_active_rids[@]+"${_active_rids[@]}"}"; do
            if [[ "$name" == *"$rid"* ]]; then
                return 0
            fi
        done
        return 1
    }

    local f
    for f in /tmp/gem5-mi300x-*.sock; do
        [[ -e "$f" ]] || continue
        if _resource_is_active "$f"; then
            echo "  active socket (skipped): $f"
            continue
        fi
        echo "  orphan socket: $f"
        found=1
    done

    for f in /dev/shm/mi300x-vram /dev/shm/mi300x-vram-* /dev/shm/cosim-guest-ram /dev/shm/cosim-guest-ram-*; do
        [[ -e "$f" ]] || continue
        if _resource_is_active "$f"; then
            echo "  active shmem (skipped): $f"
            continue
        fi
        echo "  orphan shmem: $f"
        found=1
    done

    # Un-namespaced resources created by older launchers
    if [[ -e /tmp/gem5-mi300x.sock ]]; then
        echo "  orphan un-namespaced socket: /tmp/gem5-mi300x.sock"
        found=1
    fi
    if docker ps -a --filter "status=exited" --filter "status=dead" --filter "status=created" --format '{{.Names}}' 2>/dev/null | grep -qx 'gem5-cosim'; then
        echo "  orphan un-namespaced container: gem5-cosim"
        found=1
    fi

    if [[ $found -eq 0 ]]; then
        echo "  (no orphaned resources found)"
    else
        echo "  (inventory only; delete only through a validated resource manifest)"
    fi

    return 0
}

# ---- Health Check ----

check_readiness() {
    local socket_path="$1"
    local vram_shmem="$2"
    local guest_ram_shmem="$3"
    local container_name="$4"
    local expected_vram_bytes="${5:-17179869184}"
    local expected_ram_bytes="${6:-8589934592}"

    if [[ ! -S "$socket_path" ]]; then
        echo "readiness check failed: socket $socket_path does not exist or is not a Unix socket"
        return 1
    fi

    if [[ ! -f "$vram_shmem" ]]; then
        echo "readiness check failed: VRAM shmem $vram_shmem does not exist"
        return 1
    fi
    local vram_size
    vram_size="$(stat -c%s "$vram_shmem" 2>/dev/null || echo 0)"
    if [[ "$vram_size" -ne "$expected_vram_bytes" ]]; then
        echo "readiness check failed: VRAM shmem size $vram_size != expected $expected_vram_bytes"
        return 1
    fi

    if [[ ! -f "$guest_ram_shmem" ]]; then
        echo "readiness check failed: guest RAM shmem $guest_ram_shmem does not exist"
        return 1
    fi
    local ram_size
    ram_size="$(stat -c%s "$guest_ram_shmem" 2>/dev/null || echo 0)"
    if [[ "$ram_size" -ne "$expected_ram_bytes" ]]; then
        echo "readiness check failed: guest RAM shmem size $ram_size != expected $expected_ram_bytes"
        return 1
    fi

    if [[ "$(docker inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null)" != "true" ]]; then
        echo "readiness check failed: container $container_name is not running"
        return 1
    fi

    return 0
}

# ---- Preflight Audit ----

run_preflight_audit() {
    echo "=== Preflight Resource Audit ==="
    echo "Timestamp: $(date -Iseconds)"
    echo ""

    echo "--- Docker containers (gem5-cosim) ---"
    docker ps -a --filter "name=gem5-cosim" 2>/dev/null || echo "(docker not available)"
    echo ""

    echo "--- /dev/shm (cosim-related) ---"
    ls -la /dev/shm/mi300x-vram* /dev/shm/cosim-guest-ram* 2>/dev/null || echo "(none found)"
    echo ""

    echo "--- /tmp sockets (gem5-mi300x) ---"
    ls -la /tmp/gem5-mi300x*.sock 2>/dev/null || echo "(none found)"
    echo ""

    echo "--- gem5 processes ---"
    pgrep -a gem5 2>/dev/null || echo "(none found)"
    echo ""

    echo "=== End Preflight Audit ==="
}
