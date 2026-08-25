[中文](../zh/getting-started.md)

# Getting Started

[Project README](../../README.md) | [Learning Labs](labs.md)

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
| Network | The build profile reaches the exact locked GitHub, QEMU, HashiCorp, Ubuntu ISO/snapshot, AMD amdgpu/ROCm, and GHCR endpoints |

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

The host preflight records only whether standard uppercase and lowercase proxy variables are set; it redacts their values and credentials. The build preflight also probes GitHub, QEMU, HashiCorp, the pinned Ubuntu ISO and snapshot service, AMD's amdgpu/ROCm repositories, and GHCR. If a proxy is required, configure the shell and Docker daemon through normal host policy, then rerun preflight. Never put proxy credentials in repository files, command history examples, or artifacts intended for sharing.

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

Do not invoke `lock-qemu-source` during ordinary setup: the tracked lock already contains the accepted source-archive SHA-256 and extracted source-tree fingerprint. A mismatch in either fingerprint is a provenance failure, not permission to replace the lock locally.

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

Do not unload/reload amdgpu repeatedly after `hw_init` fails, and do not use a manually repaired guest as a passing baseline. Driver initialization changes kernel and device state; retrying in place can hide the first failure. See the [known-issue playbook](reference.md#known-issue-playbook) before choosing a debug experiment.

## Run fresh-session HIP tests

The test runner accepts an exact stem from `tests/kernels/<stem>.cpp`, stages that test tree, compiles it in the guest, requires exactly one matching `[PASS]` marker and no `[FAIL]` marker, classifies the raw evidence, and verifies cleanup.

Start with the repository's `vector_add` program:

```bash
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add
```

With `COSIM_STRICT_ACCEPTANCE` unset or `0`, this is the default diagnostic
mode. It records the top-level and gem5 source state and permits an ordinary
learning run or dirty replay without requiring a clean HEAD; its result is not
a strict `cosim-matrix-verification/v2` acceptance row.

The empty `GUEST_TEST_PREFIX` also means `HSA_ENABLE_INTERRUPT=0`. Use one of only these explicit values when comparing runtime behavior:

```bash
COSIM_STRICT_ACCEPTANCE=1 GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    vector_add

COSIM_STRICT_ACCEPTANCE=1 GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo,AMDGPUDevice,MI300XCosim \
    vector_add
```

These are strict v2 acceptance commands: start them only with clean top-level
and gem5 source trees, and include the four required execution-evidence debug
flags. Record the two modes as separate experiments. The interrupt comparison
additionally enables `AMDGPUDevice` and `MI300XCosim`; mode 1 exercises
interrupt-backed HSA signaling and is not interchangeable with the mode-0
baseline. `matrix.tsv` must contain the effective value observed from the
guest; an unknown or unexpected value invalidates the row.

Current runner timeouts are 240 seconds to reach the guest login prompt, 60 seconds for the program inside the guest, and a 1,800-second host deadline for guest compilation plus execution. Override them only through `run_cosim_tests.sh` options and record the resulting command with the evidence.

Once the smoke test passes:

```bash
COSIM_STRICT_ACCEPTANCE=1 ./scripts/run_cosim_tests.sh \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    --repeat 3 vector_add
COSIM_STRICT_ACCEPTANCE=1 ./scripts/run_cosim_tests.sh \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    --all
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
| `runner-invocation.txt`, `launch-invocation.txt` | Runner/launcher argv, timeouts, Guest bridge, and effective gem5 configuration frozen before launch |
| `guest-run.sh` | Archived script sent to the Guest, including the effective `TEST_TIMEOUT_SECS` |
| `patch/source-snapshot.txt` | Top-level commit and hashes for the staged source, runner, launcher, repository diff, and untracked-file inventory/archive |
| `patch/binary-provenance.txt` | gem5 commit/subject/binary hash and exact test binary hash |
| `patch/repo-status.txt`, `patch/repo.patch` | Top-level tracked and uncommitted source state used by the row |
| `patch/gem5-status.txt`, `patch/gem5.patch` | gem5 submodule state used by the row |
| `qemu.log`, `gem5.log` | Full guest console and simulator evidence retained for diagnosis |
| `guest-provenance.json` and `guest-*` build/stat evidence | Strict join across the Guest image, kernel, m5, QEMU, locks, overlay patch, submodule, recipe, and pre/post read-only base-image state |
| `docker-inspect.json` | Effective gem5 container name, argv, running/OOM/restart state |
| `cleanup-status.txt` | Manifest-scoped resource cleanup result |

`guest-provenance.json` is a local snapshot derived from the clean-HEAD
builder, validator and locks, the live Guest metadata/seal, and recomputed
image/kernel hashes; it is not a signature or remote attestation. This
contract assumes a trusted local owner and cannot resist coordinated forgery
by an actor with the same UID, Docker/root-equivalent, or host-administrator
access.

A row is accepted only when its command explicitly sets
`COSIM_STRICT_ACCEPTANCE=1`, both source trees are clean, the runner exits 0,
`verdict.json` says `PASS` with `all_acceptance_gates_passed`, `matrix.tsv`
agrees, the effective HSA value and all three timeouts match the intended mode,
the `cosim-matrix-verification/v2` join across the manifest, invocations, Guest
script/log, and source/binary provenance passes; `gem5.log` contains an
in-window kernel launch, workgroup dispatch, `WgCompl`, and same-ID kernel
completion; and cleanup is verified.
Missing evidence is a failure, even if the HIP output looks correct. Diagnostic
mode deliberately preserves dirty provenance for learning and replay, but that
does not turn a dirty row into strict v2 acceptance. Only artifacts recording
`COSIM_STRICT_ACCEPTANCE=1` may enter the final v2 matrix.

Generated build and test evidence under `artifacts/` and local toolchains under `.local/cosim/` are Git-ignored. Preserve or archive them outside Git when a review needs durable evidence; never add generated images, binaries, or logs to a source commit.

<a id="manifest-scoped-cleanup"></a>

## Manifest-scoped cleanup

Normal runner and launcher exits clean up their own run. The following fallback
is only for an interrupted runner-owned session whose wrapper printed both the
exact run ID and artifact directory. A standalone foreground launch has no
runner-owned, run-scoped `launcher.pid`; if its normal trap was bypassed, stop
and diagnose instead of cleaning from a manifest alone.

Substitute the two printed values below. The procedure accepts only a trusted
artifact `launcher.pid`, proves that its process group runs
`scripts/cosim_launch.sh` with the same `--artifact-dir`, stops that exact
process group, and confirms that it has exited before it previews or confirms
cleanup with the exact manifest:

```bash
(
set -euo pipefail
RUN_ID="replace-with-the-printed-run-id"
ARTIFACT_DIR="replace-with-the-printed-artifact-directory"
ARTIFACT_DIR="$(realpath -e -- "$ARTIFACT_DIR")"
REPO_ROOT="$(pwd -P)"
case "$ARTIFACT_DIR" in
    "${REPO_ROOT}/artifacts/"*) ;;
    *) echo "artifact directory is outside this repository" >&2; exit 1 ;;
esac
[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ &&
   "$RUN_ID" != *..* ]] || {
    echo "invalid run ID" >&2
    exit 1
}

LAUNCH_PID_FILE="${ARTIFACT_DIR}/launcher.pid"
RUNNER_INVOCATION="${ARTIFACT_DIR}/runner-invocation.txt"
MANIFEST="/tmp/cosim-${RUN_ID}.session/resources.manifest"
[[ -f "$RUNNER_INVOCATION" && ! -L "$RUNNER_INVOCATION" ]] &&
    grep -Fxq "run_id=${RUN_ID}" "$RUNNER_INVOCATION" || {
        echo "artifact directory does not belong to this run ID" >&2
        exit 1
    }
[[ -f "$LAUNCH_PID_FILE" && ! -L "$LAUNCH_PID_FILE" ]] || {
    echo "trusted run-scoped launcher.pid is missing" >&2
    exit 1
}
read -r LAUNCH_PID < "$LAUNCH_PID_FILE"
[[ "$LAUNCH_PID" =~ ^[0-9]+$ ]] || {
    echo "invalid launcher PID" >&2
    exit 1
}

launcher_group_alive() {
    local group_rows
    group_rows="$(ps -eo pgid=)" || return 2
    awk -v wanted="$LAUNCH_PID" '
        $1 == wanted { found = 1 }
        END { exit(found ? 0 : 1) }
    ' <<< "$group_rows"
}

if [[ -d "/proc/${LAUNCH_PID}" ]]; then
    LAUNCH_PGID="$(ps -o pgid= -p "$LAUNCH_PID" | tr -d ' ')"
    [[ -r "/proc/${LAUNCH_PID}/cmdline" &&
       -r "/proc/${LAUNCH_PID}/environ" ]] || {
        echo "launcher command line or environment is unavailable" >&2
        exit 1
    }
    LAUNCH_CMD="$(tr '\0' ' ' < "/proc/${LAUNCH_PID}/cmdline")"
    [[ "$LAUNCH_PGID" == "$LAUNCH_PID" &&
       "$LAUNCH_CMD" == *"scripts/cosim_launch.sh"* &&
       "$LAUNCH_CMD" == *"--artifact-dir ${ARTIFACT_DIR}"* ]] &&
        tr '\0' '\n' < "/proc/${LAUNCH_PID}/environ" | \
            grep -Fxq "COSIM_RUN_ID=${RUN_ID}" || {
        echo "launcher PID/process group does not own this run" >&2
        exit 1
    }
    kill -TERM -- "-${LAUNCH_PID}"
    for _ in {1..15}; do
        launcher_group_alive || break
        sleep 1
    done
    if launcher_group_alive; then
        kill -KILL -- "-${LAUNCH_PID}" 2>/dev/null || true
        for _ in {1..5}; do
            launcher_group_alive || break
            sleep 1
        done
    fi
fi

if launcher_group_alive; then
    echo "launcher process group is still live; refusing cleanup" >&2
    exit 1
else
    GROUP_STATE=$?
    [[ "$GROUP_STATE" -eq 1 ]] || {
        echo "unable to prove launcher process group exit" >&2
        exit 1
    }
fi

if [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]]; then
    ./scripts/cosim_cleanup.sh --run-id "$RUN_ID" --manifest "$MANIFEST"
    ./scripts/cosim_cleanup.sh --run-id "$RUN_ID" \
        --manifest "$MANIFEST" --confirm
elif [[ -e "$MANIFEST" || -L "$MANIFEST" ]]; then
    echo "exact manifest is not a trusted regular file" >&2
    exit 1
else
    grep -qx 'result=PASS' "${ARTIFACT_DIR}/cleanup-status.txt" || {
        echo "manifest is absent without verified cleanup" >&2
        exit 1
    }
fi
)
```

If `launcher.pid` is missing, symlinked, stale, or mismatched, stop and diagnose
ownership. Never guess a PID, use a broad process kill, delete a bare container,
remove sockets with a wildcard, or recursively delete a session directory.

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
| PCI visible but driver/KFD/ROCm incomplete | End the session, preserve evidence, follow the run-scoped recovery above, and retry in a fresh session |
| HIP timeout, GPUVM, PM4, SDMA, fence, or IH failure | Use `verdict.json` reasons and the bounded raw-log windows described in the debug reference; do not accept a pass marker in isolation |
| Cleanup not verified | Treat the row as failed; use the exact manifest only after the owning launcher process group is confirmed stopped |

For signatures and source locations, continue with [Reference and Debugging](reference.md). Preserve full raw logs before extracting smaller windows; QEMU exits can be secondary to a gem5 failure.

## Local checkpoint

On 2026-08-24, this workspace had a local checkpoint covering the pinned build, guest driver/ROCm enumeration, and classified fresh-session HIP baselines. The evidence remained under ignored `artifacts/` and was not committed. This date is context, not a release guarantee: every host must reproduce its own preflight, hashes, verdict, matrix, and cleanup result.

## Next reading

- [Learning Labs](labs.md)
- [System Architecture](architecture.md)
- [Reference and Debugging](reference.md)
- [Chinese Getting Started](../zh/getting-started.md)
