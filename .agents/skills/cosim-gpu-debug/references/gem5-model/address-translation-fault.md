# Address Translation Fault Review

Use this reference when gem5 reports user translation faults, GART diagnostics,
unmapped pages, PTE anomalies, VMID/PASID mismatches, or doorbell activity near
a crash.

## Trigger Evidence

Load this reference for any of these log patterns:

```text
fatal: User translation fault
GART cosim
unmapped page
PTE[
Unknown doorbell offset
VMID
PASID
```

QEMU `error_setv`, vfio-user EOF, broken pipe, and device-lost messages are
secondary if a matching gem5 fatal line exists earlier in the same run id.

## Required Facts

Record these facts in the active evidence file before proposing a source edit:

- Program identity, run id, interrupt mode, test timeout, and exact runner
  command.
- First gem5 fatal line and the last 50-100 gem5 lines before it.
- Faulting GPU virtual address, translated physical address if present, PTE
  value, GART base, page-table base, framebuffer base/top, and VRAM size.
- VMID, PASID, queue id, queue base, doorbell offset, and packet id if present.
- Last PM4, SDMA, or HSA packet before the failing translation.
- Whether the same fault repeats across `HSA_ENABLE_INTERRUPT=0` and `1`.
- Nearest passing comparison that uses the same launch path and binary
  provenance.

## Log Extraction

Use focused searches first:

```bash
rg -n 'fatal: User translation fault|GART cosim|unmapped page|PTE\[|Unknown doorbell|VMID|PASID|doorbell|MAP|DISPATCH|SDMA|PM4' \
  <artifact>/logs/gem5.log <artifact>/logs/qemu.log
```

If the per-row gem5 log contains only a Docker lookup error, find the matching
launcher-side artifact by `run_id` under `artifacts/standalone/<run_id>/` and
copy its `logs/gem5.log` into the active task artifact directory. Treat the
per-row missing log as an evidence capture issue, not as a program result.

## Debug Flags

Choose the smallest useful flag set:

```bash
--gem5-debug AMDGPUDevice,PM4PacketProcessor
```

Add these only when the baseline evidence points there:

```bash
--gem5-debug SDMAEngine
--gem5-debug HSAPacketProcessor
--gem5-debug MI300XCosim
```

Avoid broad debug flags until a small set has proven insufficient. Large logs
can hide the first object that differs.

## Interpretation Rules

- A GART unmapped-page warning followed by `fatal: User translation fault`
  makes gem5 the first failing component unless an earlier QEMU abort exists.
- An unknown doorbell offset before the fault is adjacent evidence, not a
  source cause by itself. Tie it to a queue id, packet sequence, or VMID/PASID
  mismatch before editing.
- Symmetric results across interrupt modes reduce the likelihood of an
  interrupt-completion-only cause.
- A QEMU `error_setv` assertion after gem5 exits belongs in the notes as a
  secondary defensive failure.

## Patch Readiness

A source edit is ready only when the notes identify:

- the address or PTE object that first differs from a passing comparison
- the code path that should have created, updated, invalidated, or translated
  that object
- the verification rows, including the failing workload and one nearby passing
  workload
