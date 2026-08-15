---
name: cosim-gpu-rocm-stack
description: Read before debugging guest-side ROCm behavior such as HSA signals, HIP waits, KFD ioctls, driver parameters, PASID/VMID routing, or known cosim runtime quirks.
---

# Cosim ROCm Stack

Use this as the guest-side ROCm routing guide. It explains when a symptom
belongs to test policy, guest inspection, debug evidence, or model-side review.
Detailed runtime semantics live in `references/rocm-runtime-reference.md`; read
that file when debugging HSA signals, HIP API waits, KFD ioctls, PASID, VMID, or
driver parameters.

## Stack Overview

```text
HIP API
  -> ROCclr
    -> ROCr / HSA runtime
      -> amdgpu KFD
        -> amdgpu DRM
          -> gem5 GPU model through QEMU
```

## Routing

| Question | First route |
|---|---|
| Environment defaults or `HSA_ENABLE_INTERRUPT` | `cosim-gpu-test`, then `scripts/cosim_guest_env.sh` |
| Guest device visibility, driver load, debugfs | `cosim-gpu-guest` |
| Non-PASS row, hang, crash, or timeout | `cosim-gpu-debug` |
| Disk image package or service changes | `cosim-gpu-disk-image-edit` |
| HSA signal, HIP wait, KFD ioctl, PASID or VMID meaning | `references/rocm-runtime-reference.md` |

## Evidence To Collect

Use `cosim-gpu-guest` and `cosim-gpu-debug` to collect:

- amdgpu and KFD kernel log excerpts
- PCI device state and loaded kernel modules
- ROCm agent and memory visibility
- KFD debugfs state when signal or queue progress is suspected
- program identity, interrupt mode, verdict, matrix row, and artifact path

When runtime symbols or debug builds are needed, use repository scripts and
`cosim-gpu-disk-image-edit`; do not embed package-install procedures in this
skill.
