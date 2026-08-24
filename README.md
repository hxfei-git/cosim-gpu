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
| Run-scoped cleanup | `./scripts/cosim_cleanup.sh --run-id <id>` |

Do not replace these entry points with hand-written Docker, SCons, Packer, or QEMU commands. The wrappers enforce pinned inputs, run-scoped names, manifests, provenance, evidence capture, and cleanup checks.

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

## Local checkpoint

A local checkpoint on 2026-08-24 validated the pinned build and fresh-session guest driver/ROCm/HIP path. Its generated `artifacts/` are intentionally ignored by Git and are not a substitute for reproducing `verdict.json` on another host.

## License

See [LICENSE](LICENSE).
