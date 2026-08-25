#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck disable=SC1091
source "${COSIM_DIR}/scripts/cosim_lib.sh"

fail() {
    echo "[FAIL] invocation_contract: $*" >&2
    exit 1
}

FIXTURE_DIR="$(mktemp -d /tmp/cosim-invocation-contract.XXXXXX)"
trap 'rm -rf -- "$FIXTURE_DIR"' EXIT
OUTPUT="${FIXTURE_DIR}/words.txt"

cosim_print_shell_words > "$OUTPUT"
[[ ! -s "$OUTPUT" ]] || fail "零个参数产生了非空序列化结果"

cosim_print_shell_words "" > "$OUTPUT"
[[ "$(<"$OUTPUT")" == " ''" ]] || fail "单个空参数被错误折叠"

cosim_print_shell_words --gem5-debug "Flag One" > "$OUTPUT"
[[ "$(<"$OUTPUT")" == ' --gem5-debug Flag\ One' ]] || \
    fail "非空参数的数量、顺序、值或转义发生变化"

echo "[PASS] invocation_contract"
