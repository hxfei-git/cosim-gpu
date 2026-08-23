#!/bin/bash

# Convert the runner's literal Guest environment prefix into the one supported
# HSA interrupt value. Keep this parser strict so the prefix cannot become a
# shell-evaluation surface in generated Guest commands.
cosim_guest_hsa_interrupt() {
    local guest_test_prefix="${1:-}"

    case "$guest_test_prefix" in
        ""|HSA_ENABLE_INTERRUPT=0)
            printf '0\n'
            ;;
        HSA_ENABLE_INTERRUPT=1)
            printf '1\n'
            ;;
        *)
            printf '%s\n' \
                'GUEST_TEST_PREFIX must be empty, HSA_ENABLE_INTERRUPT=0, or HSA_ENABLE_INTERRUPT=1' \
                >&2
            return 2
            ;;
    esac
}

# Read the effective value from a serial console log. QEMU can place the marker
# after an ANSI control sequence separated only by CR, so convert CR to LF
# instead of deleting it.
cosim_guest_hsa_interrupt_from_log() {
    local log_path="$1"
    local interrupt_value

    [[ -f "$log_path" ]] || return 2
    interrupt_value="$(
        tr '\r' '\n' < "$log_path" |
            awk -F= '/^\[COSIM_ENV\] HSA_ENABLE_INTERRUPT=[01]$/ {print $2; exit}'
    )"
    [[ "$interrupt_value" == 0 || "$interrupt_value" == 1 ]] || return 1
    printf '%s\n' "$interrupt_value"
}
