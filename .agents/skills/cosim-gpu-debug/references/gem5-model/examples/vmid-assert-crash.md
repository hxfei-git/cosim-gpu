# VMID Assert Crash (June 2026)

`assert(queue_vmid)` in PM4 MAP_QUEUES caused gem5 crash, masking a QEMU
double-error bug in vfio-user proxy.c.

## When you'll hit this again

- gem5 dies with assertion failure during GPU init
- QEMU crashes with `error_setv` SIGABRT immediately after
- `fix-hsa-signal-ih-completion-2` or any branch with `assert(pkt->vmid)`
- The `fix-hsa-signal-only` branch (`8680505b71`) does NOT have this bug

## First failing mechanism

Commit `aa5d08d97773` replaced `lastVMID()` fallback with `assert(queue_vmid)`.
The amdgpu driver sends MAP_QUEUES with `pkt->vmid == 0` (by KFD design —
VMID is in the MQD, not the packet). The assertion was premature.

## Key decision path

Initially suspected QEMU `error_setv` bug as the first failure. GDB backtrace showed
the double-error in `vfio_user_recv_hdr`. But fixing QEMU only stopped the
crash — GPU still failed to init. Tracing backwards revealed gem5 was dying
first, QEMU was a secondary effect.

The lesson: always find the first component to fail, not the last visible assertion.

## Artifacts

- `artifacts/fix-hsa-signal-ih-completion-2/` — full RLCR review workspace
- `artifacts/pr4-qemu-error-setv-debug/` — QEMU GDB debugging session
- `artifacts/pr4-rlcr-signal-vmid/` — RLCR rounds for signal+VMID fix

## Cross-references

- `../vmid-pasid-architecture.md` — why pkt->vmid==0 is correct
- `../../qemu/error-setv-pattern.md` — error_setv mechanism
- `../../analysis/observable-dimensions.md` — observable dimension checklist
