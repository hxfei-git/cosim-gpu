# ROCm Runtime Reference

Read this when debugging guest-side HSA, HIP, KFD, PASID, VMID, or driver
parameter behavior.

## Driver Parameters

```text
ip_block_mask=0x67
ppfeaturemask=0
dpm=0
audio=0
ras_enable=0
discovery=2
```

`ip_block_mask` uses discovery order indexes, not `amd_ip_block_type` enum
values. `0x67` disables PSP at discovery bit 3 and SMU at discovery bit 4 while
keeping blocks needed for driver bring-up and command execution.

## HSA Signals

Signal flow:

```text
Kernel finishes
  -> GPU writes signal value
  -> CP_EOP interrupt
  -> amdgpu_ih_process()
  -> kfd_signal_event()
  -> HIP runtime observes signal change
```

Key structures:

- Signal object: 64-bit GPU-visible completion value.
- CP_EOP interrupt: end-of-pipe, carries PASID/VMID.
- TRAP interrupt: exception or fault path.

Common cosim issues:

- Signal does not complete: wrong VMID/PASID routing, stale PWC entry, or cache
  visibility issue.
- Signal completes but buffer content is stale: launch-time cache state or
  different CP/CU cache paths.
- `vector_add` remains a real signal-path baseline because it exercises memcpy,
  kernel completion, synchronization, and `hipFree`.

Guest workload environment is centralized in `scripts/cosim_guest_env.sh`.
`HSA_ENABLE_INTERRUPT=0` uses polling; `HSA_ENABLE_INTERRUPT=1` tests interrupt
routing.

## HIP API To HSA

| HIP API | HSA mechanism | Signal |
|---|---|---|
| `hipMalloc` | memory pool allocation | No |
| `hipMemcpy` | SDMA or blit copy | Yes |
| `hipLaunchKernel` | AQL dispatch packet | Yes |
| `hipFree` | memory pool free | Yes |
| `hipDeviceSynchronize` | wait on pending signals | Yes |
| `hipStreamSynchronize` | wait on queue signal | Yes |

`hipFree` involves a signal wait, so programs with `hipFree` can hang on signal
completion while smaller kernels may appear to work.

## KFD Ioctls

| ioctl | Purpose |
|---|---|
| `AMDKFD_IOC_CREATE_QUEUE` | Create HSA user-mode queue |
| `AMDKFD_IOC_DESTROY_QUEUE` | Destroy queue |
| `AMDKFD_IOC_SET_MEMORY_POLICY` | Set memory access policy |
| `AMDKFD_IOC_ALLOC_MEMORY_OF_GPU` | Allocate GPU-visible memory |
| `AMDKFD_IOC_MAP_MEMORY_TO_GPU` | Map memory into GPU page table |
| `AMDKFD_IOC_UNMAP_MEMORY_FROM_GPU` | Unmap from GPU page table |

## Known Cosim Quirks

| Symptom | Meaning | Route |
|---|---|---|
| KIQ disable timeout | Expected firmware-model gap unless paired with failure |
| DRM client EPERM | Render permissions or stale guest image |
| Device-side printf hangs | Test policy input for `cosim-gpu-test` |
| AtomBIOS null dereference | ROM visibility issue |
| gem5 container exited | Use `cosim-gpu-debug` |

## PASID And VMID

AMD IOMMU partitions PASID space:

```text
0x0000 .. 0x7FFF  system / GPU internal
0x8000 .. 0xFFFF  user processes
```

KFD allocates the first user PASID at `0x8000`. gem5 constants:

- `AMDGPU_FIRST_USER_PASID = 0x8000`
- `AMDGPU_FIRST_COMPUTE_VMID = 8`

VMID is allocated per process, not per stream. Multiple HIP streams in one
process share the same VMID. Multi-VMID testing requires multiple independent
processes.

See `../../cosim-gpu-debug/references/gem5-model/vmid-pasid-architecture.md`
for model-side VMID/PASID behavior.
