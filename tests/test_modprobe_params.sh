#!/bin/bash
# Regression test: ensure all cosim modprobe/insmod commands include the
# required parameters.  Missing ppfeaturemask/dpm/audio causes -EINVAL
# on ROCm 7.0+ (see issue #9).
#
# Two checks per file:
#   1. If AMDGPU_ARGS is defined, its definition must contain all params.
#   2. Every inline modprobe/insmod amdgpu line (with literal params, not
#      a variable reference) must also contain all params independently.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

REQUIRED_PARAMS=(
    "ip_block_mask=0x67"
    "ppfeaturemask=0"
    "dpm=0"
    "audio=0"
    "ras_enable=0"
    "discovery=2"
)

FAILED=0
CHECKED=0
MISSING=0
ALLOW_MISSING_SUBMODULE=0

if [[ "${1:-}" == "--allow-missing-submodule" ]]; then
    ALLOW_MISSING_SUBMODULE=1
    shift
fi
[[ $# -eq 0 ]] || {
    echo "Usage: $0 [--allow-missing-submodule]" >&2
    exit 2
}

check_line() {
    local file="$1"
    local lineno="$2"
    local line="$3"

    CHECKED=$((CHECKED + 1))
    for param in "${REQUIRED_PARAMS[@]}"; do
        if ! echo " $line " | tr '()"'"'"'' ' ' | grep -qF " $param "; then
            echo "FAIL: ${file}:${lineno} missing '${param}'"
            echo "  line: ${line}"
            FAILED=$((FAILED + 1))
            return
        fi
    done
}

check_file() {
    local filepath="$1"
    local contents
    local checked_before="$CHECKED"
    contents="$(cat "$filepath")"
    local lineno=0

    while IFS= read -r line; do
        lineno=$((lineno + 1))
        [[ "$line" =~ ^[[:space:]]*# ]] && continue

        if [[ "$line" =~ ^[[:space:]]*AMDGPU_ARGS= ]]; then
            check_line "$filepath" "$lineno" "$line"
            continue
        fi

        # Match modprobe/insmod amdgpu lines, but skip lines that pass
        # params via a variable (e.g., "${AMDGPU_ARGS[@]}") — those are
        # validated through the AMDGPU_ARGS definition check above.
        if echo "$line" | grep -qE '(modprobe|insmod).*amdgpu' &&
           ! echo "$line" | grep -qF 'AMDGPU_ARGS'; then
            check_line "$filepath" "$lineno" "$line"
        fi
    done <<< "$contents"

    if [[ "$CHECKED" -eq "$checked_before" ]]; then
        echo "FAIL: ${filepath} contains no amdgpu parameter definition/invocation"
        FAILED=$((FAILED + 1))
    fi
}

COSIM_SCRIPTS=(
    "$REPO_ROOT/scripts/cosim_guest_setup.sh"
    "$REPO_ROOT/gem5-resources/src/x86-ubuntu-gpu-ml/files/cosim-gpu-setup.sh"
)

for script in "${COSIM_SCRIPTS[@]}"; do
    if [[ -f "$script" ]]; then
        check_file "$script"
    else
        echo "MISSING: ${script}"
        MISSING=$((MISSING + 1))
    fi
done

echo ""
echo "Checked $CHECKED definitions/invocations, $FAILED failures, $MISSING missing files."

if [[ "$MISSING" -gt 0 && "$ALLOW_MISSING_SUBMODULE" -eq 0 ]]; then
    echo "FAIL: Required source files are missing; initialize exact submodules first."
    exit 1
fi

if [[ $FAILED -gt 0 ]]; then
    echo "FAIL: Missing required modprobe parameters (see issue #9)."
    exit 1
fi

if [[ $CHECKED -eq 0 ]]; then
    echo "WARN: No modprobe parameter definitions found."
    exit 0
fi

echo "PASS: All cosim modprobe commands include required parameters."
