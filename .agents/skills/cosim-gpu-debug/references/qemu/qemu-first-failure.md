# QEMU First-Failure Reference

Use this reference only when QEMU itself is likely the first failing component:
QEMU assertion, `error_setv` misuse, vfio-user protocol error, region access
failure, QEMU trace-only symptom, or a QEMU process exit while gem5 remains
alive and has no fatal event.

## Component check

Before calling a failure QEMU-first, compare:

- last gem5 log line and container status
- QEMU stderr and backtrace
- guest console progress marker
- socket close timing
- vfio-user trace events

If gem5 ends with `fatal`, `panic`, assertion, or container exit before QEMU
aborts, return to `cosim-gpu-debug` and treat QEMU as a secondary reporter.

## Evidence to collect

- `qemu-stderr.log`
- `qemu.log`
- QEMU trace file with focused vfio-user or MI300X events
- gdb backtrace showing the caller of `error_setv`, `abort`, or the assertion
- matching QEMU source line

Use `scripts/cosim_gdb_qemu.sh <run-id>` only as a debugger launcher. It starts
a tmux-hosted GDB session; it does not produce evidence by itself.

## Trace use

Write needed trace events to the active artifact directory and pass them through
the launcher:

```bash
--qemu-trace 'events=artifacts/<task>/scratch/qemu-trace-events.txt,file=artifacts/<task>/logs/qemu-trace.log'
```

Keep alternate binaries and wrappers explicitly labeled. Standard test runs
should use the normal provenance path.
