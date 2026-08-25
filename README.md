# cosim-gpu

[中文说明](README.zh.md)

`cosim-gpu` connects a KVM/Q35 guest in QEMU to the MI300X GPU model in gem5 through vfio-user. The supported workflow is repository-owned and evidence-first: initialize the pinned sources, run preflight, build through the wrapper, and validate a real HIP program in a fresh co-simulation session.

```text
HIP program in Guest Linux
  -> ROCm / KFD / amdgpu
  -> PCI BARs, MMIO, queues and GPU virtual memory
  -> QEMU vfio-user transport
  -> gem5 MI300X GPU model
  -> result and classified evidence returned to the host
```

The default single-GPU profile models 16 GiB of VRAM and 40 compute units, with 8 GiB of guest RAM. QEMU is not taken from the host `PATH`: [`configs/cosim/toolchain.lock`](configs/cosim/toolchain.lock) pins the repository-local QEMU 10.1.5 toolchain.

## Quick start

The host must be x86_64 Linux (native or WSL 2), with working KVM and a reachable Docker daemon. Docker group membership is root-equivalent; review the trust boundary before granting it. See the complete [Getting Started guide](docs/en/getting-started.md) for resource requirements, group refresh, WSL details, proxy checks, and recovery.

```bash
git submodule update --init --recursive

./scripts/cosim_preflight.sh build \
    --output-dir artifacts/preflight/build

./scripts/cosim_build.sh all
./scripts/cosim_build.sh status

./scripts/cosim_preflight.sh run \
    --output-dir artifacts/preflight/run

GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add
```

`run_cosim_tests.sh` creates a fresh QEMU/gem5 session, builds the exact staged test inside the guest, runs it, classifies the evidence, and cleans up its own run-scoped resources. The command prints the artifact directory. Completion means all of these agree—not merely that compilation succeeded:

- `verdict.json` reports `"outcome": "PASS"` and an acceptance reason.
- `matrix.tsv` records the program, effective interrupt mode, session, outcome, exit code, and artifact path.
- `patch/source-snapshot.txt` and `patch/binary-provenance.txt` identify the source tree and binary used.
- `cleanup-status.txt` reports verified cleanup.

Use `./scripts/run_cosim_tests.sh --all` only after the single-program smoke test passes. Each operator still receives a fresh session.

## Reproducible entry points

| Task | Supported entry point |
|---|---|
| Read-only host/build/runtime checks | `./scripts/cosim_preflight.sh host\|build\|run` |
| Pinned QEMU, gem5, m5, or guest build | `./scripts/cosim_build.sh qemu\|gem5\|m5\|guest\|all` |
| Build and provenance status | `./scripts/cosim_build.sh status` |
| Interactive co-simulation | `./scripts/cosim_launch.sh` |
| Fresh-session classified test | `./scripts/run_cosim_tests.sh <program>` |
| Run-scoped cleanup inventory (dry-run) | `./scripts/cosim_cleanup.sh --run-id <id>` |
| Ownership-gated interrupted recovery | [Validate and stop the exact launcher process group before manifest cleanup](docs/en/getting-started.md#manifest-scoped-cleanup) |

Do not replace these entry points with hand-written Docker, SCons, Packer, or QEMU commands. The wrappers enforce pinned inputs, run-scoped names, manifests, provenance, evidence capture, and cleanup checks.
The cleanup inventory is read-only. It does not authorize cleanup of a live
run; interrupted recovery must follow the linked `launcher.pid` ownership and
process-group exit gate before exact-manifest cleanup.

## Repository map

- `gem5/` — simulator and MI300X GPU model submodule.
- `gem5-resources/` — guest image recipe, kernel, ROCm payload, and workloads submodule.
- `configs/cosim/` — lockfiles and co-simulation configuration.
- `scripts/` — preflight, build, launch, test, classification, audit, and cleanup entry points.
- `tests/kernels/` — HIP integration programs; `tests/common/` contains shared helpers.
- `docs/en/` and `docs/zh/` — paired English and Chinese documentation.

## Documentation and labs

- [Getting Started](docs/en/getting-started.md) — host setup, reproducible build, launch, validation, evidence, and cleanup.
- [Learning labs](docs/en/labs.md) — source-guided experiments for PCI/BAR/MMIO, memory translation, queues, PM4, SDMA, interrupts, and HIP dispatch.
- [Architecture](docs/en/architecture.md) — transport, memory sharing, GPUVM/GART, DMA, and MSI-X data paths.
- [Reference and debugging](docs/en/reference.md) — parameters, source map, known limitations, and diagnostic signatures.

| Learning topic | 中文 | English |
|---|---|---|
| PCI / BAR / MMIO | [中文](docs/zh/labs.md#lab-pci-bar-mmio) | [English](docs/en/labs.md#lab-pci-bar-mmio) |
| amdgpu / KFD initialization | [中文](docs/zh/labs.md#lab-amdgpu-kfd-init) | [English](docs/en/labs.md#lab-amdgpu-kfd-init) |
| VRAM / GTT / GART / GPUVM | [中文](docs/zh/labs.md#lab-vram-gtt-gart-gpuvm) | [English](docs/en/labs.md#lab-vram-gtt-gart-gpuvm) |
| Ring / Queue / Doorbell | [中文](docs/zh/labs.md#lab-ring-queue-doorbell) | [English](docs/en/labs.md#lab-ring-queue-doorbell) |
| PM4 | [中文](docs/zh/labs.md#lab-pm4) | [English](docs/en/labs.md#lab-pm4) |
| SDMA | [中文](docs/zh/labs.md#lab-sdma) | [English](docs/en/labs.md#lab-sdma) |
| Fence / IH / MSI-X | [中文](docs/zh/labs.md#lab-fence-ih-msix) | [English](docs/en/labs.md#lab-fence-ih-msix) |
| HIP → KFD/amdgpu → GPU dispatch | [中文](docs/zh/labs.md#lab-hip-dispatch) | [English](docs/en/labs.md#lab-hip-dispatch) |
| gem5 GPU model and debugging | [中文](docs/zh/labs.md#lab-gem5-debug) | [English](docs/en/labs.md#lab-gem5-debug) |

## Local checkpoint

A local checkpoint on 2026-08-24 validated the pinned build and fresh-session guest driver/ROCm/HIP path. Its generated `artifacts/` are intentionally ignored by Git and are not a substitute for reproducing `verdict.json` on another host.

## License

See [LICENSE](LICENSE).
