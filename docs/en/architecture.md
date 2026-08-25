[中文](../zh/architecture.md)

# Co-simulation Architecture

This document describes the implementation scope in this repository; measured
boundaries are called out separately, and experimental code is identified even
when no accepted runtime evidence exists. It deliberately separates physical
AMD behavior, gem5 model behavior, and co-simulation compatibility code. For
runnable exercises, see [labs.md](labs.md); for build and diagnostic commands,
see [reference.md](reference.md).

## 1. Reading conventions and evidence baseline

The following labels apply throughout the document:

- `[REAL AMD]` describes the Linux amdgpu/KFD and ROCm design. The matching
  Guest packages are pinned, but their complete source trees are not vendored
  here; canonical upstream paths and function names are therefore navigation
  anchors, not claims about local line numbers.
- `[GEM5]` describes code in the `gem5/` submodule.
- `[COSIM]` describes QEMU/KVM, vfio-user, shared-memory integration, launch
  policy, or a compatibility workaround in this repository.

The checked-in code defaults to one synthetic `1002:74a0` endpoint, 16 GiB of
VRAM, 8 GiB of Guest RAM, and 40 modeled CUs. The preserved runtime evidence
under `artifacts/amd-gpu-learning-env/tests/` proves:

| Evidence | Proven observation |
|---|---|
| `phase3-driver-002/phase3-verdict.json` | PCI endpoint and BARs, amdgpu binding, `/dev/kfd`, DRM render nodes, ROCm 7.0 and a `gfx942` agent |
| `phase4-baseline-vector-add-i0/dispatch-verdict.json` | AQL task 2, grid 4352, workgroup size 256, workgroups 0–16, and gem5 kernel completion |
| `phase4-interrupt-vector-add-i1/interrupt-verdict.json` | signal 1→0, IH cookie/write-pointer update, and vfio-user IRQ vector 0 at the same gem5 tick |

These artifacts prove this configuration and binary provenance. They do not
prove timing accuracy or unmeasured behavior of a physical MI300X.

## 2. System boundary

### 2.1 Process topology

```text
Host Linux / WSL2
  QEMU 10.1.5, Q35 + KVM
    Guest Linux 6.8.0-79
      HIP application
      ROCm userspace / ROCr
      KFD + amdgpu
    QEMU vfio-user-pci client
              |
              | vfio-user Unix socket: config, MMIO, doorbells, IRQ
              v
  Docker container
    gem5 VEGA_X86, StubWorkload
      MI300XVfioUser
      AMDGPUDevice / AMDGPUVM
      PM4PacketProcessor / SDMAEngine / AMDGPUInterruptHandler
      HSAPacketProcessor / HWScheduler
      GPUCommandProcessor / GPUDispatcher / Shader / CUs
      GPU_VIPER Ruby memory hierarchy

Shared mappings
  run-scoped Guest RAM shmem  <-> QEMU memory-backend-file / gem5 backstore
  run-scoped VRAM shmem       <-> BAR0 / gem5 device-memory backstore
```

`[COSIM]` QEMU executes the Guest CPU and kernel with KVM. gem5 does not boot a
second Linux kernel: its `StubWorkload` hosts only the GPU-side objects.
`MI300XVfioUser` is the vfio-user server and QEMU's built-in
`vfio-user-pci` is the client. No project-specific QEMU device patch is in the
runtime path.

### 2.2 Transport channels

| Channel | Carries | Implementation |
|---|---|---|
| vfio-user socket | PCI configuration, BAR2 doorbells, BAR5 MMIO, reset and IRQ protocol | `[COSIM]` `MI300XVfioUser` + libvfio-user ↔ QEMU `vfio-user-pci` |
| VRAM shared memory | BAR0 bytes, page tables and device-local allocations | `[COSIM]` per-run `/dev/shm/mi300x-vram-<run>`; mmap-able BAR0 and gem5 VRAM backstore |
| Guest RAM shared memory | GTT/system pages, queues, signals, fences and IH buffers | `[COSIM]` per-run `/dev/shm/cosim-guest-ram-<run>`; QEMU `memory-backend-file,share=on` and gem5 `system.shared_backstore` |

The socket is a control and notification path, not a bulk-memory coherence
protocol. Shared files provide byte visibility, while gem5's Ruby hierarchy
models GPU-side traffic. The combination is useful for functional study but is
not equivalent to physical CPU/GPU cache coherence.

### 2.3 Session and disk isolation

`[COSIM]` Every launch receives a run ID, unique socket/shared-memory names, a
resource manifest, and an artifact directory. The raw Guest image is treated
as immutable and used as the backing file of a run-scoped qcow2 overlay.
Cleanup is allowed only through the validated manifest. This prevents any
successful or failed Guest session from silently changing the next run's base
image.

An interrupted-session fallback has an additional ownership precondition. Use
only the trusted, run-scoped `launcher.pid` to validate the launcher process
group and its `scripts/cosim_launch.sh` command line, stop that exact process
group, and confirm that it has exited. Only then may the still-present exact
manifest be passed to `cosim_cleanup.sh`. If the PID file is absent, symlinked,
stale, or does not match the recorded artifact directory, stop and diagnose;
never guess a PID or use a broad kill.

The implementation is in
[`scripts/cosim_launch.sh`](../../scripts/cosim_launch.sh),
[`scripts/cosim_lib.sh`](../../scripts/cosim_lib.sh), and
[`scripts/cosim_cleanup.sh`](../../scripts/cosim_cleanup.sh).

## 3. PCI endpoint, BARs, and MMIO

### 3.1 Measured BAR layout

The Phase 3 Guest probe measured this exact layout:

| BAR | Size | Role | Access path |
|---|---:|---|---|
| BAR0+1 | 16 GiB | prefetchable VRAM aperture | mmap of the VRAM shared-memory fd; callback fallback exists |
| BAR2+3 | 2 MiB | non-prefetchable doorbell aperture | vfio-user callback → `AMDGPUDevice::writeDoorbell` |
| BAR4 | 8 KiB | MSI-X table and PBA, 256 vectors advertised | handled by libvfio-user/QEMU; model raises vector 0 today |
| BAR5 | 512 KiB | GPU MMIO registers | vfio-user callback → `AMDGPUDevice::readMMIO`/`writeMMIO` |

`[COSIM]` The endpoint sets vendor/device/subsystem IDs to `1002:74a0`, PCI
class `0x030000`, 64-bit type bits for BAR0 and BAR2, and an expansion ROM
window. BAR sizes are defined by `MI300XVfioUser::BAR*_SIZE`; do not infer them
from old documents or from a physical board datasheet.

### 3.2 Access flow

```text
Guest amdgpu MMIO or doorbell access
  -> QEMU vfio-user-pci
  -> MI300XVfioUser::{handleMmioAccess,handleDoorbellAccess}
  -> AMDGPUDevice::{writeMMIO,writeDoorbell}
  -> NBIO / GPUVM / PM4 / SDMA / IH / HSA queue owner
```

`AMDGPUDevice::writeDoorbell` looks up the registered doorbell offset and
routes it by `QueueType`: PM4 graphics/compute, SDMA gfx/page/RLC, Compute AQL,
or IH read-pointer update. An unknown offset is preserved as a pending
doorbell; it is evidence to correlate with queue setup, not proof of a root
cause by itself.

Relevant model sources:

- [`mi300x_vfio_user.cc`](../../gem5/src/dev/amdgpu/mi300x_vfio_user.cc):
  `initVfuContext`, `setupBars`, BAR callbacks, `sendIrqRaise`.
- [`amdgpu_device.cc`](../../gem5/src/dev/amdgpu/amdgpu_device.cc):
  `writeDoorbell`, `writeMMIO`, `setDoorbellType`, VMID/PASID maps.

## 4. amdgpu and KFD initialization

### 4.1 Layered initialization flow

```text
Linux PCI core enumerates 1002:74a0 and assigns BARs
  -> cosim-gpu-setup.service publishes the ROM and discovery firmware
  -> amdgpu_pci_probe / amdgpu_device_init
  -> selected IP blocks initialize GMC/GART, IH, GFX and SDMA
  -> amdgpu exposes DRM nodes and hands the device to KFD
  -> KFD creates topology, process/queue and doorbell interfaces
  -> ROCr enumerates the KFD agent
  -> HIP sees gfx942
```

`[REAL AMD]` Useful driver anchors are:

- `drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c`: `amdgpu_pci_probe`.
- `drivers/gpu/drm/amd/amdgpu/amdgpu_device.c`: `amdgpu_device_init` and IP
  block lifecycle.
- `drivers/gpu/drm/amd/amdgpu/amdgpu_discovery.c`: IP-discovery parsing.
- `drivers/gpu/drm/amd/amdkfd/kfd_device.c` and `kfd_chardev.c`: KFD device
  creation and userspace ioctl entry points.

Match these names against the DKMS version in `configs/cosim/guest.lock`; the
repository does not vendor that complete source.

### 4.2 Co-simulation policy

`[COSIM]` The Guest boots with amdgpu blacklisted so the systemd service can
publish the synthetic ROM and `mi300_discovery` data first. The service then
loads amdgpu with:

```text
ip_block_mask=0x67 ppfeaturemask=0 dpm=0 audio=0 ras_enable=0 discovery=2
```

This selects the subset implemented by the model and disables PSP, SMU, power
management, audio and RAS paths that require absent firmware/hardware. It is a
co-simulation boot contract, not a recommended physical-GPU configuration.
VCN/JPEG are also not part of the compute acceptance path.

The discovery data reports a larger topology than gem5 instantiates: the
measured Guest reports `active_cu_number 320`, while the default gem5 command
creates 40 CUs. Driver-visible topology proves that ROCm accepts the synthetic
device; it must not be used as the modeled CU count or as a performance claim.

## 5. VRAM, GTT, GART, and GPUVM

### 5.1 Terms are not interchangeable

| Term | Meaning in this environment |
|---|---|
| VRAM | Device-local allocation domain and BAR0 aperture; 16 GiB is configured, while the driver reports approximately 16383 MiB usable |
| GTT | Driver placement domain backed by Guest/system RAM; the Phase 3 probe reported about 3970 MiB ready |
| GART | VMID0 aperture/page table mapping GPU-visible addresses to system pages; the probe reported a 512 MiB PCIe GART |
| GPUVM | Per-process GPU virtual address spaces selected by VMID and associated with PASID; user mappings use multilevel GPU page tables |

GTT describes where memory resides; GART and GPUVM describe how addresses are
translated. “GTT table” should not be used as a synonym for GART.

### 5.2 Shared backstores and physical addresses

`[COSIM]` For the default 8 GiB Guest, Q35 leaves a PCI hole: gem5 mirrors the
configured layout with 2 GiB below 4 GiB and the remainder above 4 GiB.
System-memory DMA addresses must therefore retain Guest physical semantics.
The IH ring base and write-pointer addresses are system DMA addresses and do
not pass through the GART translation generator.

VRAM has a different path. `MI300XVfioUser::setupSharedMemory` creates/maps the
VRAM object, exposes its fd through BAR0, gives the pointer to `AMDGPUVM`, and
connects GPU page-table walkers to that mapping. The gem5 device-memory
backstore uses the same per-GPU name.

### 5.3 VMID0 GART and user GPUVM

`[GEM5]` `AMDGPUVM` records the aperture and page-table registers programmed by
the driver.

- `GARTTranslationGen::translate` performs the VMID0 single-level lookup. It
  first checks the model's `gartTable` shadow and can then read the PTE from
  shared VRAM.
- `UserTranslationGen::translate` selects the page-table base for a user VMID
  and invokes the Vega page-table walker. A PTE `system` bit selects system
  memory; a device-local result is converted through the MMHUB aperture.
- `PM4PacketProcessor::mapProcess` allocates a VMID for a PASID and installs
  the process page-table base. Doorbells and queues retain the VMID needed by
  later DMA and completion operations.
- `MMHUBTranslationGen::translate` converts local VRAM offsets to the model's
  global VRAM address, which matters in multi-GPU configurations.

Read [`amdgpu_vm.cc`](../../gem5/src/dev/amdgpu/amdgpu_vm.cc) together with
[`pagetable_walker.cc`](../../gem5/src/arch/amdgpu/vega/pagetable_walker.cc),
not in isolation from PM4/queue ownership.

### 5.4 Translation and coherence boundaries

`[COSIM]` Two compatibility paths are intentionally not hardware semantics:

1. Page-table walkers can read PTEs directly from the VRAM shared mapping when
   normal model memory visibility is insufficient.
2. A missing GART PTE or a faulted timing walk can map to physical address
   zero to keep the simulator alive.

The second behavior is a dangerous semantic-loss fallback. Address zero is
valid Guest RAM in the configured model; reads may be wrong and writes may be
discarded or corrupt unrelated data. Any occurrence must remain visible in
the artifact and must not be described as a safe sink or as successful
translation.

TLB invalidation, page-walk-cache state, SQC/GL2 maintenance, Ruby traffic and
direct shared-memory visibility are distinct mechanisms. A passing small
kernel does not prove full cache-coherence semantics.

## 6. Rings, queues, and doorbells

There are two related but different command planes:

| Plane | Producer and payload | Modeled consumer |
|---|---|---|
| Kernel/management plane | amdgpu/KFD PM4 packets in KIQ/runlist/compute management rings | `PM4PacketProcessor` |
| User compute plane | ROCr writes 64-byte AQL packets into a process queue | `HWScheduler` + `HSAPacketProcessor` |

`[REAL AMD]` KFD creates process queues, maps doorbell pages and programs MQDs.
ROCr updates an AQL write index and rings the mapped doorbell without issuing a
new ioctl for every dispatch.

`[GEM5]` PM4 `MAP_PROCESS` establishes PASID/VMID state; `MAP_QUEUES` reads an
MQD, registers the queue with `HSAPacketProcessor::setDeviceQueueDesc`, and
associates its doorbell. `HWScheduler::write` receives later user doorbells,
updates queue state and schedules AQL processing. `HSAPacketProcessor` DMAs
packets from the Guest queue and advances read/dispatch pointers.

The word “ring” therefore needs an owner: PM4 ring, SDMA ring, AQL queue, and
IH ring have different packet formats and completion rules.

## 7. PM4 model

### 7.1 Role in the HIP path

PM4 is essential for driver initialization, process mapping, queue creation,
runlists, memory writes and synchronization. It is not accurate to describe a
HIP kernel body as being converted into PM4 and executed by
`PM4PacketProcessor`. The compute dispatch payload consumed by this model is
an AQL kernel-dispatch packet; PM4 prepares and manages the queue that carries
it.

```text
KFD/amdgpu management ring doorbell
  -> AMDGPUDevice::writeDoorbell
  -> PM4PacketProcessor::process
  -> decodeNext / decodeHeader
  -> mapProcess / mapQueues / runList / indirectBuffer / writeData /
     releaseMem / waitRegMem / queryStatus
```

The correct entry point is `PM4PacketProcessor::process`; `processPkt` belongs
to `HSAPacketProcessor` and must not be used as the PM4 function name.

### 7.2 Scope of modeling

The implementation covers the packets needed by the current stack but does
not provide bit-for-bit or timing-complete MI300X command-processor behavior.
Unsupported packet variants may warn, panic, or use simplified completion.
Queue, VMID, cache-maintenance and release semantics must be tested per
workload. See
[`pm4_packet_processor.cc`](../../gem5/src/dev/amdgpu/pm4_packet_processor.cc)
and [`pm4_defines.hh`](../../gem5/src/dev/amdgpu/pm4_defines.hh).

## 8. SDMA model

`[REAL AMD]` SDMA engines execute independent copy/write/fill, page-table and
synchronization packets. A queue doorbell tells an engine that its write
pointer advanced.

`[GEM5]` The default configuration creates 16 `SDMAEngine` objects. MMIO setup
registers gfx/page queues and their doorbell offsets; `processGfx`,
`processPage`, or `processRLC` begins decoding. Implemented handlers include
`write`, `copy`, `indirectBuffer`, `fence`, `trap`, `pollRegMem`, `ptePde`,
`atomic`, and `constFill`, with operation-specific limitations.

SDMA addresses must be classified before access: raw local VRAM, MMHUB/device
memory, VMID0 GART/system memory, or user GPUVM. The model uses
`getDeviceAddress`, `getGARTAddr`, and `translate`; a failure close to SDMA is
not automatically an SDMA decoder bug—it can be queue ownership or address
translation.

An SDMA fence packet writes a value and advances the queue. A separate trap
packet posts an IH event. “Fence” and “interrupt” are not synonyms.

## 9. Fence, completion signal, IH, and MSI-X

Synchronization has several layers:

- `[REAL AMD]` A driver fence represents ordered completion and can be backed
  by a sequence value plus an interrupt/wakeup path.
- `[GEM5]` PM4 `RELEASE_MEM` and SDMA `fence` write completion data for their
  respective engines.
- `[GEM5]` HSA kernel completion decrements the AQL completion signal through
  `GPUCommandProcessor::sendCompletionSignal`; this is separate from a PM4 or
  SDMA fence object.

For an interruptible HSA signal, the measured model path is:

```text
GPUCommandProcessor::updateHsaSignalData writes signal 1 -> 0
  -> AMDGPUInterruptHandler::prepareInterruptCookie (CP_EOP, PASID/VMID)
  -> submitInterruptCookie writes a 256-bit entry to the Guest IH ring
  -> submitWritePointer updates the Guest IH write-pointer location
  -> AMDGPUDevice::intrPost
  -> MI300XVfioUser::sendIrqRaise(0)
  -> libvfio-user / QEMU inject MSI-X into the Guest
  -> amdgpu/KFD consumes the event and wakes ROCr
```

The Guest consumes the IH ring and updates its read pointer through the IH
doorbell. `[COSIM]` Only CP_EOP and TRAP_ID sources are modeled here. CP_EOP
cookies clamp a low model VMID into the driver's compute-VMID range (8–15)
while retaining the PASID derived from the real model mapping. BAR4 advertises
256 vectors, but the bridge currently raises vector 0.

Polling (`HSA_ENABLE_INTERRUPT=0`) proves completion without requiring this
wakeup chain. Interrupt mode (`1`) is a separate experiment and must show the
signal, IH writes and MSI-X evidence—not merely a final `[PASS]` line.

## 10. HIP to gem5 end-to-end dispatch

### 10.1 Real software-stack view

```text
HIP source compiled for gfx942
  -> HIP runtime (amdhip64)
  -> ROCr/HSA discovers the KFD agent and creates an AQL queue
  -> KFD/amdgpu establishes process VM, memory mappings, MQD and doorbell
  -> ROCr writes kernel object, kernargs and a 64-byte AQL dispatch packet
  -> ROCr publishes the queue write index and rings the doorbell
  -> GPU executes the kernel
  -> completion signal is decremented
  -> poll or KFD event wakes the waiting host thread
  -> HIP copies/checks the result
```

KFD/amdgpu is involved in device, VM, memory and queue setup, but the normal
per-dispatch fast path is a userspace AQL write plus a doorbell, not one KFD
ioctl per kernel.

### 10.2 gem5/cosim view

```text
Guest AQL queue and buffers in shared Guest RAM / VRAM
  -> QEMU forwards BAR2 doorbell through vfio-user
  -> AMDGPUDevice routes ComputeAQL doorbell
  -> HWScheduler selects the registered queue
  -> HSAPacketProcessor DMAs and decodes AQL packet
  -> GPUCommandProcessor::submitDispatchPkt reads code object and kernargs
  -> GPUCommandProcessor::dispatchPkt
  -> GPUDispatcher assigns workgroups to Shader/CUs
  -> CU execution and Ruby GPU memory traffic
  -> GPUDispatcher completion
  -> GPUCommandProcessor decrements HSA completion signal
  -> polling or IH/MSI-X completion path
  -> Guest HIP code observes data and returns PASS
```

The preserved vector trace ties one exact source SHA to Task 2, a 4352-thread
grid, workgroup size 256, all 17 workgroups, HSA completion and kernel
completion. Two transient pending-invalidate launch retries were retained in
the verdict rather than hidden. This is the minimum kind of evidence needed to
claim GPU-model execution; compilation and Guest output alone are insufficient.

## 11. Multi-GPU and xGMI boundary

The configuration can construct multiple GPU devices, each with its own
vfio-user bridge, VRAM mapping, shader, PM4, SDMA and IH objects. The CUs are
wired into one gem5 process and a shared disjoint VIPER hierarchy. Optional
`XGMIBridge` objects support ring or mesh routing with configured bandwidth and
latency.

This is `[GEM5]` experimental modeling, not a validated physical xGMI fabric.
The normal launcher exposes `--num-gpus` but not the xGMI topology knobs, and
the accepted single-endpoint baseline does not establish xGMI coherence,
ordering, peer-DMA or performance fidelity. Also distinguish multiple
synthetic PCI endpoints from the multiple XCP/DRM nodes exposed by one Guest
device's discovery topology.

## 12. Source navigation map

| Topic | Model/integration source | Key functions or objects |
|---|---|---|
| Runtime construction | `gem5/configs/example/gpufs/mi300_cosim.py` | `_create_per_gpu_components`, `buildCosimSystem` |
| vfio-user/PCI/BAR/IRQ | `gem5/src/dev/amdgpu/mi300x_vfio_user.{cc,hh}` | `initVfuContext`, `setupBars`, `setupSharedMemory`, `sendIrqRaise` |
| Device routing | `gem5/src/dev/amdgpu/amdgpu_device.{cc,hh}` | `writeDoorbell`, `writeMMIO`, `allocateVMID`, `mapDoorbellToVMID`, `intrPost` |
| GPU virtual memory | `gem5/src/dev/amdgpu/amdgpu_vm.{cc,hh}` | GART, MMHUB and user translation generators |
| Page walk/TLB | `gem5/src/arch/amdgpu/vega/pagetable_walker.cc`, `tlb.cc` | functional/timing PTE walks, PWC and TLB |
| PM4 | `gem5/src/dev/amdgpu/pm4_packet_processor.{cc,hh}` | `process`, `decodeHeader`, `mapProcess`, `mapQueues`, `releaseMem` |
| SDMA | `gem5/src/dev/amdgpu/sdma_engine.{cc,hh}` | queue registration, decode and operation handlers |
| AQL queue | `gem5/src/dev/hsa/hw_scheduler.{cc,hh}`, `hsa_packet_processor.{cc,hh}` | `HWScheduler::write`, `getCommandsFromHost`, `processPkt` |
| Dispatch | `gem5/src/gpu-compute/gpu_command_processor.cc`, `dispatcher.cc`, `shader.cc` | `submitDispatchPkt`, `dispatchPkt`, `GPUDispatcher::dispatch` |
| Interrupt | `gem5/src/dev/amdgpu/interrupt_handler.{cc,hh}` | cookie, IH ring, write pointer and post |
| HIP/CLR launch and synchronization | `clr/hipamd/src/hip_platform.cpp`, `hip_module.cpp`, `hip_stream.cpp`, `rocclr/device/rocm/rocvirtual.cpp` | `ihipLaunchKernel`, `ihipModuleLaunchKernel`, `VirtualGPU::submitKernel`, `dispatchAqlPacket`, `hipStreamSynchronize_common` |
| ROCr queue/doorbell/signal | `ROCR-Runtime/runtime/hsa-runtime/core/runtime/{amd_gpu_agent,amd_aql_queue,hsa}.cpp` | `GpuAgent::QueueCreate`, `AqlQueue::AddWriteIndex*`, `StoreRelease`, `hsa_signal_wait_scacquire` |
| Launch/evidence | `scripts/cosim_launch.sh`, `run_cosim_tests.sh`, `classify_runs.py` | isolated run, raw evidence, verdict and cleanup |

For `[REAL AMD]` source navigation, start with the driver paths in Section 4,
then `amdgpu_vm.c`, `amdgpu_gart.c`, `amdgpu_ring.c`,
`amdgpu_doorbell_mgr.c`, `amdgpu_fence.c`, `amdgpu_ih.c`, and KFD's
`kfd_process_queue_manager.c`. The table gives the complete HIP/CLR and ROCr
entry points. Their paths and functions were checked against the
`rocm-7.0.0` tag, but every anchor must still be verified against the immutable
revision matching the pinned Guest package. Do not substitute current upstream
behavior without recording the version change.

## 13. Known limits and non-claims

- Synthetic PCI config space, ROM, discovery data, reset and error behavior
  are not physical MI300X behavior.
- PSP, SMU, RAS, DPM, audio and media blocks are disabled or outside scope.
- Driver-visible 320 CUs and eight partitions/render nodes are not the 40 CUs
  instantiated by the default model.
- PM4, SDMA, IH sources, firmware scheduling, cache maintenance, atomics and
  page-fault recovery are partial and workload-dependent.
- Direct PTE reads, VMID clamping, invalidate ACK shortcuts and address-zero
  fallbacks are cosim compatibility mechanisms, not hardware contracts.
- Shared-memory visibility plus Ruby does not establish full heterogeneous
  coherence, and gem5 timing is not calibrated here to MI300X performance.
- Multi-GPU/xGMI code exists, but the accepted baseline does not prove physical
  topology, peer-memory or link fidelity.
- A PASS is valid only for the exact source, binaries, environment, logs,
  cleanup and verdict preserved by the runner. See [reference.md](reference.md)
  for the artifact-first acceptance contract.
