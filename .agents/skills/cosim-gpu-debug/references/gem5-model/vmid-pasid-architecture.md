# VMID and PASID Architecture

Reference for VMID/PASID semantics beyond the basics covered in the main skill.
Consolidated from review and debugging sessions on `fix-hsa-signal-ih-completion-2`
and `fix-hsa-signal-only` (June 2026).

## VMID field duality

Every queue carries two VMID-derived fields with different sources, different
uses, and the potential to diverge:

| Field | Source | Set at | Used for |
|-------|--------|--------|----------|
| `q->vmid()` (`PM4Queue::_vmid`) | `captured_vmid` from `newQueue()` | `mapQueues` via `lastVMID()` or explicit `vmid` arg | Doorbell mapping, interrupt routing, HSA queue descriptors, checkpoint — **this is what gem5 routes on** |
| `mqd->hqd_vmid` (`QueueDesc`) | MQD DMA read from `CP_HQD_VMID` register | MQD writeback after `MAP_QUEUES` | DPRINTF logging, checkpoint serialization — **not used for routing** |

**Risk**: The two fields can diverge. KIQ/PQ set `hqd_vmid` via MMIO but
`newQueue()` without an explicit vmid argument falls back to `lastVMID()`,
potentially routing internal queue interrupts to a user process.

`q->vmid()` is the authoritative routing field. When debugging, cross-check
both fields and verify they agree for the queue under investigation.

## Why `pkt->vmid == 0` in MAP_QUEUES is correct

Cross-validated against Linux KFD driver source. The amdgpu driver hardcodes
`PACKET3_MAP_QUEUES_VMID(0)` in the KIQ ring buffer:

```c
// Linux drivers/gpu/drm/amd/amdgpu/gfx_v9_0.c:961
amdgpu_ring_write(kiq_ring,
    PACKET3_MAP_QUEUES_VMID(0) |
    ...);
```

VMID is not carried in the MAP_QUEUES packet. It is set in the MQD (Memory-mapped
Queue Descriptor) and transferred via DMA:

```c
// Linux drivers/gpu/drm/amd/amdkfd/kfd_mqd_manager_v9.c:321
m->cp_hqd_vmid = q->vmid;
```

The KFD driver allocates VMID per-process:

```c
// kfd_device_queue_manager.c
static int allocate_vmid(..., struct qcm_process_device *qpd, ...) {
    if (list_empty(&qpd->queues_list))
        retval = allocate_vmid(dqm, qpd, q);
    q->properties.vmid = qpd->vmid;  // all queues share the process VMID
}
```

Therefore `lastVMID()` as a fallback in `newQueue()` was always a temporal
coincidence — "the most recently allocated VMID" happens to be "my VMID" in
single-process mode, but this is not a semantic derivation.

## `lastVMID()` elimination

The `lastVMID()` method (`amdgpu_device.hh`) was a crutch used in three places:

| Call site | Original | Correct fix |
|-----------|----------|-------------|
| `newQueue()` (PM4) | `q->vmid(vmid ? vmid : gpuDevice->lastVMID())` | `q->vmid(vmid)` — KIQ/PQ explicitly pass VMID 0 |
| `mapQueues()` (PM4) | `lastVMID()` for doorbell offset mapping | `captured_vmid` captured before async MQD DMA |
| `releaseMemDone()` (PM4) | `lastVMID()` for signal write | `q->vmid()` — queue already owns its VMID |

`captured_vmid` is the correct fix for `mapQueues`: capture the value at
packet-decode time before the async MQD DMA callback fires, because another
MAP_PROCESS could change `lastVMID()` between decode and callback.

## PASID address space

AMD IOMMU partitions the 16-bit PASID space:

```
0x0000 ─────────────── 0x7FFF ─────────────────────── 0xFFFF
  ↑ System/GPU reserved    ↑ User process PASID
  (GFX engine, SDMA,       (first user = 0x8000)
   IH ring, VCN, etc.)
```

| Range | Purpose |
|-------|---------|
| `0x0000 – 0x7FFF` | System / GPU internal: hardware engines (GFX, SDMA, VCN), IH ring |
| `0x8000 – 0xFFFF` | User processes: KFD allocates one PASID per ROCm process |

In cosim single-process mode, `cookie->pasid = 0x8000` is the first user PASID
and `cookie->vmId = 8` corresponds to `AMDGPU_FIRST_COMPUTE_VMID` (KFD allocates
VMIDs in range 8–15 for MI300X).

The constants are defined in `interrupt_handler.hh`:

```cpp
constexpr uint32_t AMDGPU_FIRST_USER_PASID  = 0x8000;
constexpr uint32_t AMDGPU_FIRST_COMPUTE_VMID = 8;
```

## SDMA RLC per-queue VMID

The original code hardcoded `cur_vmid = 1` in SDMA RLC processing. Real hardware
writes the VMID to `sdmax_rlcx_rb_cntl.RB_VMID` from queue properties.

Fix applied (`sdma_engine.hh/.cc`):
- `SDMAQueue` gains a `_vmid` field with getter/setter
- `registerRLCQueue` accepts a `uint16_t vmid` parameter and calls `queue.setVmid(vmid)` for both RLC0 and RLC1
- `processRLC` reads `cur_vmid = queue.vmid()` instead of the hardcoded `1`

## KIQ/PQ internal queue VMID routing

KIQ (Kernel Interface Queue) and PQ (Privileged Queue) are internal queues
that execute with VMID 0. `newQueue()` must receive an explicit `vmid = 0`
for these queues; using `lastVMID()` routes their interrupts to whatever
user process last called MAP_PROCESS.

## Multi-VMID testing

VMID is allocated per-process, not per-stream. Multiple HIP streams within one
process share the same VMID. Multi-VMID requires multiple independent processes.

rocm-examples has no multi-VMID test cases. A custom shell script that launches
two HIP programs asynchronously is the minimal test:

```bash
./hip_program_a &
PID_A=$!
./hip_program_b &
PID_B=$!
wait $PID_A $PID_B
```

This is an open gap — multi-VMID paths in the model are not currently exercised
by automated tests.

## Cross-references

- `discovery-log.md` entry 6: interrupt VMID routing for multi-operation programs
- `vmid-assert-lesson.md`: `assert(queue_vmid)` crash chain and QEMU secondary failure
- `cache-coherence-checkpoints.md`: source-level checkpoints for TLB/PWC/cache
- `../../../cosim-gpu-rocm-stack/SKILL.md`: HSA signals and KFD ioctls
