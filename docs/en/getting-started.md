# Getting Started

[中文](../zh/getting-started.md) | [Project README](../../README.md) | [Learning Labs](labs.md)

This guide is the public, reproducible path from a new checkout to a classified HIP execution through Guest Linux, amdgpu/KFD/ROCm, QEMU vfio-user, and the gem5 MI300X model. Use the repository wrappers shown here. They encode the source locks, build provenance, run-scoped resources, evidence contract, and cleanup rules that ad-hoc commands omit.

## Supported workflow

```text
submodules
  -> host/build preflight
  -> pinned QEMU + gem5 + m5 + guest build
  -> runtime preflight
  -> one fresh QEMU/gem5 session per HIP program
  -> verdict + matrix + provenance + verified cleanup
```

A successful compilation is an intermediate result. Acceptance requires the complete runtime path and a classifier-produced `PASS` with matching provenance and cleanup evidence.

Do not build or launch this stack with direct Docker, SCons, Packer, or QEMU commands. Do not invent fixed container names, sockets, or shared-memory paths. The wrappers generate a run ID and a resource manifest so concurrent and interrupted runs remain attributable.

## Host and WSL requirements

### Platform and resources

`cosim_preflight.sh` enforces the following host baseline:

| Requirement | Required state |
|---|---|
| OS and architecture | Linux on x86_64 |
| CPU | At least 2 online host CPUs |
| Memory | At least 12 GiB total host RAM |
| Workspace | At least 80 GiB free on the repository filesystem |
| Virtualization | `/dev/kvm` exists and is readable/writable by the current process |
| Runtime storage | `/dev/shm` and `/tmp` exist and are writable |
| Containers | Docker daemon is reachable and reports amd64/x86_64 |
| Network | GitHub, QEMU downloads, and GHCR are reachable for a build profile |

Native Linux is the simplest host. WSL must be WSL 2 and must expose a usable `/dev/kvm` to Linux. Enabling CPU virtualization in BIOS/UEFI, enabling nested virtualization, restarting Windows, and restarting WSL are host-owner actions; the repository cannot perform them. This project does not require or automatically edit `.wslconfig`.

The default launch uses 8 GiB of guest RAM, 4 guest CPUs, one GPU with 16 GiB of modeled VRAM, and 40 modeled compute units. These are guest/model settings, not substitutes for the host minimums above.

### Dependency and access wrapper

Audit first. The audit is read-only and reports installed packages, WSL/native Linux, resources, Docker, KVM, and account group state:

```bash
./scripts/cosim_host_setup.sh audit --for-user "$USER"
```

On a Debian/Ubuntu host with systemd, ask the wrapper to print the exact privileged plan before installing anything:

```bash
./scripts/cosim_host_setup.sh plan --for-user "$USER"
```

The installation action must run as root. It installs the repository's fixed package set and enables Docker, but leaves group membership unchanged by default:

```bash
sudo ./scripts/cosim_host_setup.sh install --for-user "$USER"
```

If the host owner has explicitly accepted both memberships, add `--grant-runtime-groups` to the `plan` and `install` actions. The `kvm` group grants access to hardware virtualization. The `docker` group is a stronger trust boundary: it is effectively root-equivalent because members can ask the daemon to mount or modify host resources.

```bash
./scripts/cosim_host_setup.sh plan --for-user "$USER" \
    --grant-runtime-groups
sudo ./scripts/cosim_host_setup.sh install --for-user "$USER" \
    --grant-runtime-groups
```

The setup wrapper never reads credentials, changes sudoers, edits WSL/Windows configuration, or stores proxy values. Any sudo authentication is handled by the host's normal privilege broker, outside the script.

If Docker is supplied by an external host integration such as Docker Desktop, do not blindly run the systemd installation action. Use `audit`, configure the provider deliberately, and continue only when preflight can reach its amd64 daemon.

### Group refresh

Adding an account to `docker` or `kvm` does not update existing shells, terminals, IDEs, or an already-running Codex process.

- On native Linux, end the login session completely and sign in again.
- On WSL, close Linux shells, run `wsl --shutdown` from Windows PowerShell, then relaunch the distribution. This is a manual Windows boundary.
- Do not infer access from account membership alone. The later preflight checks the permissions of the process that will actually launch co-simulation.

After opening a fresh session, run:

```bash
./scripts/cosim_host_setup.sh verify --for-user "$USER"
```

Then run the `host` preflight below. The two checks are complementary: setup verification checks packages and account configuration; preflight checks current-process KVM and Docker access plus resource limits.

### Network and proxies

The host preflight records only whether standard uppercase and lowercase proxy variables are set; it redacts their values and credentials. The build preflight also probes GitHub, `download.qemu.org`, and GHCR. If a proxy is required, configure the shell and Docker daemon through normal host policy, then rerun preflight. Never put proxy credentials in repository files, command history examples, or artifacts intended for sharing.

## Initialize pinned sources

From the repository root:

```bash
git submodule update --init --recursive
git submodule status --recursive
```

Both `gem5/` and `gem5-resources/` must match the gitlinks recorded by the top-level commit. Do not update either submodule merely to solve a local build problem; that changes the experiment identity and must be reviewed as a source change.

## Run preflight

Use repository-relative artifact directories. Preflight refuses output outside `artifacts/` and writes human-readable evidence plus JSON when requested.

```bash
./scripts/cosim_preflight.sh host \
    --output-dir artifacts/preflight/host

./scripts/cosim_preflight.sh build \
    --output-dir artifacts/preflight/build
```

| Profile | What it checks |
|---|---|
| `host` | Linux/x86_64, CPUs, RAM, disk, KVM, Docker, temporary storage, proxy state, and download endpoints |
| `build` | All host checks plus pinned submodules, compilers/tools, and QEMU development libraries |
| `run` | Runtime host checks plus pinned QEMU provenance/features, gem5, m5/guest assets, disk image, and stale-resource safety |

Add `--json` when machine-readable preflight evidence is needed. A required `FAIL` or `UNKNOWN` returns exit code 1; invalid arguments return 2. Do not build past a required preflight failure—use its check ID and remediation text to repair the host, then rerun the same profile.

## Reproducible build

[`configs/cosim/toolchain.lock`](../../configs/cosim/toolchain.lock) pins QEMU 10.1.5, its official source identity, and signature key. The build wrapper installs it under `.local/cosim/qemu/10.1.5/`; launch prefers that repository-local binary instead of a host `PATH` copy. Guest inputs are independently pinned by [`configs/cosim/guest.lock`](../../configs/cosim/guest.lock).

### Build actions

| Action | Behavior |
|---|---|
| `status` | Read-only report of QEMU, gem5, m5, and guest paths, metadata, hashes, and readiness |
| `qemu` | Verify the lock and build repository-local QEMU 10.1.5 incrementally |
| `gem5` | Build the repository Docker images and `VEGA_X86/gem5.opt` with provenance |
| `m5` | Ensure gem5 is built, build the x86 m5 utility, and stage it into guest files |
| `guest` | Ensure QEMU and m5 are ready, then build and validate the pinned guest image and kernel |
| `all` | Execute the complete dependency chain through `guest` |

The normal full build is:

```bash
./scripts/cosim_build.sh status
./scripts/cosim_build.sh all
./scripts/cosim_build.sh status
```

For a focused build, replace `all` with `qemu`, `gem5`, `m5`, or `guest`. `--force` reruns the selected normal incremental path and never deletes build trees, for example:

```bash
./scripts/cosim_build.sh gem5 --force
```

QEMU, gem5, and m5 build parallelism defaults to 4 jobs. Tune only through `QEMU_BUILD_JOBS`, `GEM5_BUILD_JOBS`, and `M5_BUILD_JOBS` on the wrapper invocation; reducing jobs is the first response to host memory pressure.

Do not invoke `lock-qemu-source` during ordinary setup: the tracked lock already contains the accepted source SHA-256. A mismatch is a provenance failure, not permission to replace the lock locally.

After the build, require a runtime preflight:

```bash
./scripts/cosim_preflight.sh run \
    --output-dir artifacts/preflight/run
```

Proceed only when required checks pass and `status` identifies the pinned local QEMU, standard gem5 binary, staged m5, guest image, and guest kernel.

## Launch and inspect a guest

For an interactive architecture/debugging session, use:

```bash
./scripts/cosim_launch.sh
```

The launcher assigns a unique run ID, creates run-scoped socket/container/shared-memory names, records a resource manifest, starts gem5 in its runtime image, waits for model readiness, and starts QEMU/KVM in the foreground. Its default artifact directory is `artifacts/standalone/<generated-run-id>`.

The Guest auto-login console should eventually show the `cosim-gpu-setup.service` path completing. Observational checks printed by the launcher include `rocm-smi` and `rocminfo`; expected identity is the modeled AMD device with a `gfx942` agent. For the acceptance path, also expect PCI enumeration, an amdgpu-bound device, `/dev/kfd` and DRM nodes, and no fatal GPUVM/PM4/SDMA/IH error before the HIP result.

Press `Ctrl-A X` to leave QEMU. Allow the launcher trap to capture logs and verify cleanup. Interactive launch is useful for inspection, but it is not a classified test and does not replace the fresh-session runner.

### Partial driver initialization

If PCI enumeration succeeds but amdgpu initialization, KFD, or ROCm only partially appears, treat that guest as contaminated evidence:

1. Preserve the printed run ID and artifact directory.
2. Exit through the launcher so its manifest-scoped cleanup runs.
3. Inspect the captured QEMU console, gem5 log, launcher category, and cleanup status.
4. Start a new session for every retry.

Do not unload/reload amdgpu repeatedly after `hw_init` fails, and do not use a manually repaired guest as a passing baseline. Driver initialization changes kernel and device state; retrying in place can hide the first failure. See [Known Issues and Pitfalls](reference.md#4-known-issues-and-pitfalls) before choosing a debug experiment.

## Run fresh-session HIP tests

The test runner accepts an exact stem from `tests/kernels/<stem>.cpp`, stages that test tree, compiles it in the guest, requires exactly one matching `[PASS]` marker and no `[FAIL]` marker, classifies the raw evidence, and verifies cleanup.

Start with the repository's `vector_add` program:

```bash
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add
```

The empty `GUEST_TEST_PREFIX` also means `HSA_ENABLE_INTERRUPT=0`. Use one of only these explicit values when comparing runtime behavior:

```bash
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add

GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh vector_add
```

Record the two modes as separate experiments. Mode 1 exercises interrupt-backed HSA signaling and is not interchangeable with the mode-0 baseline. `matrix.tsv` must contain the effective value observed from the guest; an unknown or unexpected value invalidates the row.

Current runner timeouts are 240 seconds to reach the guest login prompt, 60 seconds for the program inside the guest, and a 1,800-second host deadline for guest compilation plus execution. Override them only through `run_cosim_tests.sh` options and record the resulting command with the evidence.

Once the smoke test passes:

```bash
./scripts/run_cosim_tests.sh --repeat 3 vector_add
./scripts/run_cosim_tests.sh --all
```

`--repeat` creates a fresh session for each iteration. `--all` discovers the sorted `tests/kernels/*.cpp` set and creates a fresh child session and artifact directory for every program; the current set is `gemm`, `histogram`, `multi_gpu_verify`, `prefix_scan`, `reduction`, `transpose`, and `vector_add`. A later source change can change this set, so the directory and archived source snapshot remain authoritative.

Avoid `--keep-alive` for acceptance rows. It deliberately preserves a live session and therefore cannot satisfy verified-cleanup acceptance until that session is closed and cleaned.

## Evidence and acceptance

The runner prints the exact artifact directory. A custom `--output-dir` is allowed only below the repository's `artifacts/` directory and must be empty; use a new directory for every row.

| Evidence | Meaning |
|---|---|
| `verdict.json` | Authoritative classifier outcome, primary reason, all reasons, identity checks, and evidence completeness |
| `matrix.tsv` | Program, effective HSA interrupt value, run/session, outcome, exit code, reason, and artifact path |
| `runner-metadata.txt` | Exact program/source/binary identity, expected environment, compile/test exit codes, markers, and cleanup state |
| `patch/source-snapshot.txt` | Top-level commit and hashes for the staged source, runner, repository diff, and untracked-file inventory/archive |
| `patch/binary-provenance.txt` | gem5 commit/binary hash and exact test binary hash |
| `patch/repo-status.txt`, `patch/repo.patch` | Top-level tracked and uncommitted source state used by the row |
| `patch/gem5-status.txt`, `patch/gem5.patch` | gem5 submodule state used by the row |
| `qemu.log`, `gem5.log` | Full guest console and simulator evidence retained for diagnosis |
| `cleanup-status.txt` | Manifest-scoped resource cleanup result |

A row is accepted only when the runner exits 0, `verdict.json` says `PASS` with `all_acceptance_gates_passed`, `matrix.tsv` agrees, the effective HSA value matches the intended mode, exact source/binary provenance is present, and cleanup is verified. Missing evidence is a failure, even if the HIP output looks correct.

Generated build and test evidence under `artifacts/` and local toolchains under `.local/cosim/` are Git-ignored. Preserve or archive them outside Git when a review needs durable evidence; never add generated images, binaries, or logs to a source commit.

## Manifest-scoped cleanup

Normal runner and launcher exits clean up their own run. If a process was interrupted, use the exact run ID printed by the wrapper. First preview the owned resources:

```bash
RUN_ID=replace-with-the-printed-run-id
./scripts/cosim_cleanup.sh --run-id "$RUN_ID"
```

The first command is a dry run. If the manifest, run ID, paths, and container labels are correct, confirm the same scope:

```bash
./scripts/cosim_cleanup.sh --run-id "$RUN_ID" --confirm
```

The cleanup wrapper accepts only the validated `/tmp/cosim-<run-id>.session/resources.manifest` ownership model and verifies removal. Never substitute broad process kills, bare container deletion, wildcard socket removal, or recursive deletion. If no unique valid manifest exists, stop and diagnose ownership instead of guessing.

## Learning labs

After the mode-0 `vector_add` baseline passes, continue with the paired [AMD GPU Driver / Architecture Learning Labs](labs.md). The labs use real repository code and classified runs to cover:

- PCI configuration, BARs, and MMIO transport.
- amdgpu discovery and initialization.
- VRAM, GTT, GART, GPUVM, and address translation.
- Rings, queues, doorbells, PM4, and SDMA.
- Fences, IH, MSI-X, and HSA signaling.
- The HIP → ROCm/KFD/amdgpu → GPU dispatch chain.
- gem5 GPU-model debug points and cosim-specific transport/workarounds.

Each lab distinguishes real AMD GPU behavior, gem5 modeling, and cosim-gpu-specific implementation. Keep the [Architecture](architecture.md) data-flow diagrams and [Reference](reference.md) source map open while running them.

## Troubleshooting order

Diagnose the first failing layer and keep the original artifact directory intact:

| Symptom | First action |
|---|---|
| Host, KVM, Docker, disk, or network failure | Rerun the matching `cosim_preflight.sh` profile and use its check ID/remediation |
| QEMU/gem5/m5/guest build failure | Preserve the wrapper's build log/provenance, check `cosim_build.sh status`, then retry only the failing build action |
| Model never becomes ready | Inspect the run's `gem5.log`, launcher category, and manifest; do not start QEMU separately |
| Guest never reaches login | Inspect `qemu.log` together with `gem5.log`; keep the same run ID for attribution |
| PCI visible but driver/KFD/ROCm incomplete | End the session, preserve evidence, clean by manifest, and retry in a fresh session |
| HIP timeout, GPUVM, PM4, SDMA, fence, or IH failure | Use `verdict.json` reasons and the bounded raw-log windows described in the debug reference; do not accept a pass marker in isolation |
| Cleanup not verified | Treat the row as failed and use only `cosim_cleanup.sh` with its exact manifest |

For signatures and source locations, continue with [Reference and Debugging](reference.md). Preserve full raw logs before extracting smaller windows; QEMU exits can be secondary to a gem5 failure.

## Local checkpoint

On 2026-08-24, this workspace had a local checkpoint covering the pinned build, guest driver/ROCm enumeration, and classified fresh-session HIP baselines. The evidence remained under ignored `artifacts/` and was not committed. This date is context, not a release guarantee: every host must reproduce its own preflight, hashes, verdict, matrix, and cleanup result.

## Next reading

- [Learning Labs](labs.md)
- [System Architecture](architecture.md)
- [Reference and Debugging](reference.md)
- [Chinese Getting Started](../zh/getting-started.md)
