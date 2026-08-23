#!/bin/bash
# Manifest-scoped cosim cleanup. Dry-run unless --confirm is supplied.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/cosim_lib.sh"

RUN_ID=""
MANIFEST=""
CONFIRM=0

usage() {
    echo "Usage: $0 --run-id ID [--manifest PATH] [--confirm]"
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --confirm) CONFIRM=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "invalid run ID"
[[ "$RUN_ID" != *..* ]] || die "invalid run ID"

if [[ -z "$MANIFEST" ]]; then
    mapfile -t candidates < <(find /tmp -maxdepth 2 -type f \
        -path "*/cosim-${RUN_ID}.session/resources.manifest" -print 2>/dev/null)
    [[ ${#candidates[@]} -eq 1 ]] || \
        die "expected one manifest for run ${RUN_ID}; found ${#candidates[@]}"
    MANIFEST="${candidates[0]}"
fi

MANIFEST="$(realpath -e -- "$MANIFEST")"
[[ "$MANIFEST" == "/tmp/cosim-${RUN_ID}.session/resources.manifest" ]] || \
    die "manifest path does not belong to run ${RUN_ID}"

# Consumed dynamically by cleanup_from_manifest from the sourced library.
# shellcheck disable=SC2034
COSIM_MANIFEST_FILE="$MANIFEST"
CONTAINER="$(cosim_container_name "$RUN_ID")"

echo "Run ID: $RUN_ID"
echo "Manifest: $MANIFEST"
echo "Container: $CONTAINER"
while IFS='|' read -r role type path; do
    [[ "$role" == "runtime" ]] || continue
    printf '  %-10s %s\n' "$type" "$path"
done < "$MANIFEST"

if [[ "$CONFIRM" -eq 0 ]]; then
    echo "Dry-run only; pass --confirm to remove these project-owned resources."
    exit 0
fi

cleanup_from_manifest "$CONTAINER"
verify_cleanup 10 "$CONTAINER" || die "cleanup verification failed"
echo "Cleanup verified for run ${RUN_ID}."
