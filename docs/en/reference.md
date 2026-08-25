[中文](../zh/reference.md)

# Co-simulation Reference and Debug Handbook

This is the operational lookup guide for the repository-owned MI300X
co-simulation flow. It is artifact-first: a console PASS or a successful build
is never accepted without program identity, simulator logs, provenance and
verified cleanup. Read [architecture.md](architecture.md) for mechanisms and
[labs.md](labs.md) for guided experiments.

## 1. Operating rules

1. Run commands from the top-level repository.
2. Use `scripts/cosim_build.sh`, `cosim_launch.sh`,
   `run_cosim_tests.sh`, `cosim_cleanup.sh` and the repository evidence tools.
   Do not replace them with ad hoc Docker, SCons, QEMU or `/dev/shm` commands.
3. Give every run a unique safe `COSIM_RUN_ID` and a new, empty artifact
   directory below `artifacts/`.
4. Use one fresh QEMU+gem5 session per operator. The launcher uses a per-run
   qcow2 overlay, so the raw Guest image remains a backing image.
5. Preserve the first failing run. Classify it before rebuilding or editing
   source; rerun the same target after a fix.
6. `artifacts/` is local and ignored by Git. Copy unique evidence before any
   workspace cleanup.

The host setup wrapper never reads or changes `.wslconfig`, Windows/BIOS
settings, proxy configuration, sudoers or credentials. Adding an account to
the `docker` group is an explicit security decision because it provides
root-equivalent host control.

## 2. Canonical workflow

### 2.1 Host and build checks

Read-only host and runtime checks:

```bash
./scripts/cosim_host_setup.sh audit --for-user "$(id -un)"
./scripts/cosim_host_setup.sh verify --for-user "$(id -un)"

PREFLIGHT_ID="preflight-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run --json \
    --output-dir "artifacts/amd-gpu-learning-env/preflight/${PREFLIGHT_ID}"
```

The reproducible build interface is:

```bash
./scripts/cosim_build.sh status
./scripts/cosim_build.sh all
```

Focused actions are `qemu`, `gem5`, `m5`, and `guest`. The normal wrapper is
incremental and hash-gated. `--force` reruns an action without deleting its
build tree. A cold Guest build is expensive and may resolve packages within
the locked recipe; it promises functional, not byte-for-byte, reproducibility.

### 2.2 One accepted HIP run

Strict `cosim-matrix-verification/v2` acceptance is opt-in. Begin with clean
top-level and gem5 source trees and set `COSIM_STRICT_ACCEPTANCE=1` explicitly:

```bash
RUN_ID="vector-poll-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --boot-timeout 240 \
    --test-timeout 60 \
    --guest-run-timeout 1800 \
    --output-dir "artifacts/amd-gpu-learning-env/tests/${RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    vector_add
```

Only the empty prefix, `HSA_ENABLE_INTERRUPT=0`, and
`HSA_ENABLE_INTERRUPT=1` are accepted. Empty means `0`. The runner prints and
records the effective value; the requested prefix alone is not evidence.

For the interrupt comparison, use a different run and artifact directory:

```bash
RUN_ID="vector-irq-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/tests/${RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo,AMDGPUDevice,MI300XCosim \
    vector_add
```

### 2.3 Regression and repetition

The fixed operator set is enumerated by the runner. Each child gets a fresh
session:

```bash
RUN_ID="regression-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh --all \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    --output-dir "artifacts/amd-gpu-learning-env/tests/${RUN_ID}"
```

Use repeat mode for nondeterminism, never an in-Guest loop:

```bash
RUN_ID="vector-repeat-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh --repeat 3 \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo \
    --output-dir "artifacts/amd-gpu-learning-env/tests/${RUN_ID}" \
    vector_add
```

### 2.4 Standalone learning launch and cleanup

An interactive launch is useful for observation, but it is not an accepted
operator result because it lacks the runner's program contract:

```bash
RUN_ID="inspect-$(date +%Y%m%d-%H%M%S)"
COSIM_RUN_ID="$RUN_ID" ./scripts/cosim_launch.sh \
    --artifact-dir "artifacts/amd-gpu-learning-env/standalone/${RUN_ID}" \
    --gem5-debug MI300XCosim,AMDGPUDevice
```

Normal exit invokes manifest-scoped cleanup automatically. This foreground
launch does not create a runner-owned, run-scoped `launcher.pid`; if its normal
trap is bypassed, do not clean from a manifest alone. Stop and diagnose the
still-owned session. For an interrupted runner-owned session, follow the
[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup): it
validates the exact `scripts/cosim_launch.sh` process group and artifact
directory through `launcher.pid`, stops and confirms exit of that group, and
only then permits `cosim_cleanup.sh` with the exact manifest.

Do not guess a PID or delete sockets, containers, shared memory, or session
directories by a global name/glob. `cosim_launch.sh --force-clean` is
deliberately a dry-run; unscoped confirmed deletion is refused.

## 3. Wrapper interfaces and defaults

### 3.1 Build and preflight

| Interface | Supported values / meaning |
|---|---|
| `cosim_preflight.sh host|build|run` | host inventory; build prerequisites and pins; complete runtime assets |
| `cosim_build.sh status` | report QEMU, gem5, m5 and Guest metadata/hash state |
| `cosim_build.sh qemu|gem5|m5|guest|all` | repository-owned incremental build actions |
| `cosim_build.sh ... --force` | rerun the selected action; never deletes the build tree |

Build provenance is kept below `.local/cosim/build/` and copied into runtime
artifacts. QEMU source identity is locked by `configs/cosim/toolchain.lock`;
Guest recipe/package identities are in `configs/cosim/guest.lock`.

### 3.2 Launcher defaults

| Option | Default / contract |
|---|---|
| QEMU | pinned `.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64` when present |
| gem5 | `gem5/build/VEGA_X86/gem5.opt`, container image `gem5-run:local` |
| `--host-mem`, `--host-cpus` | `8G`, `4` |
| `--vram-size`, `--num-cus` | `16GiB`, `40` CUs per modeled GPU |
| `--num-gpus` | `1` |
| `--timeout` | 120 seconds for gem5 listener readiness |
| socket | `/tmp/gem5-mi300x-<run-id>.sock`; custom paths outside this run-scoped form are refused |
| Guest disk | raw base plus `/tmp/cosim-<run-id>.session/guest-overlay.qcow2` |
| Guest RAM / VRAM | run-scoped files in `/dev/shm` |
| `--artifact-dir` | must resolve below the repository's `artifacts/` directory |
| `--gem5-debug` | comma-separated gem5 debug flags |
| `--qemu-trace` | QEMU trace event expression, for example `vfio_user_*` |
| `--share-dir` | optional 9p host directory; resolved path must exist |

The launcher serializes access with `.local/cosim/runtime.lock`; concurrent
runtime sessions are intentionally rejected.

### 3.3 Runner defaults

| Option / value | Default / constraint |
|---|---|
| `--boot-timeout` | 240 seconds |
| `--test-timeout` | 60 seconds inside the Guest |
| `--guest-run-timeout` | 1800-second host deadline for compile plus test |
| `--all` | every `tests/kernels/*.cpp` operator, one fresh session each |
| `--repeat N` | same exact operator, N fresh sessions |
| `--keep-alive` | diagnostic only; incompatible with repeat mode |
| operator | exact lowercase kernel stem such as `vector_add`; substring matching is not accepted |
| `COSIM_STRICT_ACCEPTANCE` | `0`/unset: diagnostic mode and dirty replay are allowed; `1`: strict v2 acceptance, both source trees must be clean, and `--gem5-debug` must include `HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo` |

Unknown `--name VALUE` options are passed to `cosim_launch.sh`. Each output
directory must be absent or empty and must not be a symlink. Diagnostic mode
still archives dirty provenance; it does not silently promote a dirty run to
strict acceptance.

## 4. Locked and measured identities

The following values describe the preserved 2026-08-23 baseline, not an
automatic promise for a later rebuild:

| Component | Identity |
|---|---|
| QEMU source | 10.1.5; archive SHA-256 `1f1209b4db82e6c4417eaf6e7e0b073563572a042d9fb7492b084ba65a9c0693`; source-tree fingerprint `9e2d43798bdfe7baaa7e8413ddbc35fdf409c8b435a47e5f5d435af4fd25d4b1` |
| QEMU binary | SHA-256 `89eccd422cac9ce206171a31ec1f5db963a3c76c2b3d8e8f53d1ebd058a9a5eb` |
| Guest OS/kernel | Ubuntu 24.04.2 recipe; `6.8.0-79-generic` |
| ROCm / DKMS | `7.0.0.70000-38~24.04`; `1:6.14.14.30100000-2204008.24.04` |
| gem5 source | `4c1f90498f89e15a3797cb50e9b534164bc57536` |
| gem5 binary | SHA-256 `a395b7efdaef1067223bf1e3d82780f0bdde190bee99735b12e10c377e1777a1` |
| m5 | SHA-256 `1fa0fa253551eea1f1921560c92b2028146d630bef896266664865c5e2b15256` |
| GPU identity | synthetic `1002:74a0`, `gfx942`; 16 GiB configured VRAM, 40 modeled CUs |

The Phase 4 trace rows were collected before the current qcow2-overlay
hardening. They remain valid dispatch/interrupt mechanism evidence for their
recorded source and binary, but they are not proof of the current launcher's
disk-isolation contract. New acceptance runs must also archive
`guest-overlay.json`, `guest-provenance.json`, `guest-build-meta.txt`,
`guest-content-seal.txt`, the build inputs, and pre/post-run stats. The final
verifier fully hashes the current raw base image once and joins it to the seal,
kernel, m5, QEMU, submodule gitlink, and build recipe; any inode, ctime, mtime,
or size drift across the run fails acceptance.

The trust boundary is the owner of the local repository and build directory.
The clean HEAD anchors the tracked builder, validator, and input locks; the
final verifier fully rehashes the Guest image and kernel. The live
`guest-content-seal.txt` is the trust root for that local build output. Strict
v2 therefore detects stale evidence, partial replacement, and runtime drift,
but does not claim to resist a malicious actor who can write the workspace and
coherently forge every local build artifact. Cross-party attestation requires
exporting the final matrix, seal, and matching Git commit to independent
read-only or signed storage, which is outside the local learning environment's
acceptance scope.

## 5. Artifact acceptance contract

### 5.1 Per-run evidence

An operator artifact normally contains:

| Role | Expected file |
|---|---|
| exact command/environment | `runner-invocation.txt`, `launch-invocation.txt`, `runner-metadata.txt`, `guest-run.sh` |
| Guest and QEMU stream | `qemu.log` |
| gem5 stream | `gem5.log` |
| program result | `verdict.json`, `classifier-output.json`, local `matrix.tsv` |
| source identity | `patch/source-snapshot.txt`, repo/gem5 status and patch files |
| binary identity | `patch/binary-provenance.txt` |
| lifecycle | `runner-category.txt`, `launcher-category.txt`, `cleanup-status.txt` |
| resource state | process, socket and `/dev/shm` snapshots |
| Guest build and disk isolation | `guest-overlay.json`, `guest-provenance.json`, `guest-build-meta.txt`, `guest-content-seal.txt`, `guest.lock`, `guest-overlay.patch`, `guest-base-stat.txt`, `guest-base-stat-pre.json`, `guest-base-stat-post.json` |
| container identity | name, command, arguments, running state, OOM state, and restart state in `docker-inspect.json` |

Strict v2 PASS requires `COSIM_STRICT_ACCEPTANCE=1`, clean top-level and gem5
source trees, and all of the following from one artifact: exact program
identity, compile exit 0, test exit 0, exactly one `[PASS] <program>`, no FAIL
marker or timeout/early simulator exit, one effective HSA interrupt value,
agreement between the manifest and effective invocation/timeouts, complete
source/binary provenance, full QEMU/gem5 evidence, and verified cleanup. An
accepted `gem5.log` must also show, inside that row's test window, a same-kernel
launch, workgroup dispatch, `WgCompl`, and kernel completion sequence, with
argv matching `docker-inspect.json`. An ordinary learning or dirty-replay run defaults to diagnostic mode and is not a
strict v2 PASS. Only artifacts recording `COSIM_STRICT_ACCEPTANCE=1` may enter
the final v2 matrix.

`scripts/classify_runs.py` intentionally rejects a bare PASS marker. Its
primary reasons include `program_identity_mismatch`, `compile_failure`,
`timeout`, `simulator_early_exit`, `nonzero_test_exit`,
`fail_marker_present`, `invalid_pass_marker_count`, `cleanup_failure`, and
`evidence_incomplete`.

### 5.2 Audit, classify, and verify

Build compact indexes before opening large logs:

```bash
ARTIFACT="artifacts/amd-gpu-learning-env/tests/<run-id>"
python3 scripts/cosim_artifact_audit.py \
    --root "$ARTIFACT" --out "$ARTIFACT/audit" --json
python3 scripts/classify_runs.py \
    --artifact-dir "$ARTIFACT" --program vector_add --json
```

The audit preserves raw logs and produces source-attributed tables including
`row_status.tsv`, `verdicts.tsv`, `provenance.tsv`,
`log_availability.tsv`, `signals.tsv`, `review_queue.tsv`, and
`raw_read_plan.tsv`. Read `raw_read_plan.tsv` before selecting raw log windows.

For a frozen matrix, verify exact one-to-one joins between accepted manifest
rows, top-level matrix rows, per-row verdicts, metadata, hashes and replayable
source snapshots:

```bash
python3 scripts/verify_cosim_matrix.py \
    --manifest artifacts/amd-gpu-learning-env/tests/run-manifest.tsv \
    --matrix artifacts/amd-gpu-learning-env/matrix.tsv \
    --output artifacts/amd-gpu-learning-env/matrix-verification.json
```

The manifest records intent and acceptance status; it is not runtime evidence
by itself.
The current strict output schema is `cosim-matrix-verification/v2`. A historical
row missing `runner-invocation.txt`, `launch-invocation.txt`, or `guest-run.sh`
cannot be relabeled as current acceptance evidence.

### 5.3 Measured dispatch checkpoints

The polling vector artifact records:

- exact source SHA-256
  `c195ff32bada2bd8acce4f9361a9fb515c4f468fd86116746eb2030a0df17ff5`;
- flags `HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo`;
- one matching AQL Task 2, grid 4352, workgroup size 256;
- workgroups 0–16, HSA completion and `Completed kernel 2`;
- two visible transient invalidate retries.

The interrupt artifact adds flags `AMDGPUDevice,MI300XCosim` and records signal
1→0, IH ring/cookie/write-pointer events, and IRQ vector 0 at gem5 tick
`783006015986249`. These are useful comparison anchors; line numbers and hashes
live in each artifact's trace verdict and should not be copied into new-run
claims.

## 6. Artifact-first failure routing

### 6.1 First failing component

Start with `verdict.json`, the local matrix row and audit tables. Then route by
the first durable event:

| First durable evidence | Primary owner / next evidence |
|---|---|
| preflight or `readiness_fail` | host access, pin, image, socket or shared-memory readiness before Guest analysis |
| `gem5_init_timeout` or early container exit | gem5 command/provenance and beginning/end of `gem5.log` |
| `boot_timeout` with live QEMU | Guest boot/serial state; do not edit GPU code yet |
| compile/loader error before workload output | staged source/binary and Guest ROCm environment |
| gem5 fatal, panic or assertion | gem5 is primary; later QEMU EOF/socket errors are secondary |
| `User translation fault`, missing GART PTE, VMID/PASID mismatch | address, PTE, page-table base, queue/doorbell owner and nearest passing row |
| no progress after an AQL doorbell | queue read/write/dispatch pointers, packet type and HSA scheduler |
| workgroups advance but test times out | throughput/timeout budget or later completion/signal state, not an immediate functional edit |
| result mismatch after kernel completion | memory domain, copy/SDMA, cache visibility and exact output check |
| signal reaches zero but host waits | polling versus mailbox/event, IH ring/write pointer and MSI-X evidence |
| `cleanup_fail` | preserve the artifact; validate and stop its run-scoped launcher process group, confirm exit, then and only then use the exact manifest |

QEMU `error_setv`, broken pipe, EOF or device-lost output after an earlier gem5
fatal is a secondary propagation symptom. Do not patch QEMU until same-run
evidence shows it failed first.

### 6.2 Minimum comparison record

Before a source edit, record:

- failing run ID, exact operator/source/binary, interrupt value and timeouts;
- first durable failure line and a bounded preceding window;
- nearest passing row with matching provenance, or why none exists;
- first differing object: packet/dispatch/queue ID, doorbell offset, signal
  address, VMID, PASID, GPU virtual address, physical address or PTE;
- whether progress counters still change and whether diagnostics covered the
  final object range;
- source function that owns that transition and the exact same-target rerun.

If a diagnostic filters queue IDs, workgroups, packet IDs or addresses, record
its covered and observed ranges. Absence outside the filter is
`coverage_insufficient`, not proof that an event did not occur.

<a id="known-issue-playbook"></a>

### 6.3 Historical failure-signature index

The table maps exact strings from old logs to the current workflow. It is not a
current root-cause assertion and does not authorize reuse of historical manual
fixes. If source, binary, Guest, or environment provenance differs, the cause
remains unknown.

| Old ID | Searchable signature | Current disposition and acceptance meaning |
|---|---|---|
| 4.1 | `Unable to locate a BIOS ROM`, NULL dereference in `amdgpu_atom_parse_data_header` | Guest setup now owns ROM injection; recurrence is a setup/provenance failure, and an acceptance session must not be repaired by writing `/dev/mem` manually |
| 4.2 | `PSP load tmr failed`, `hw_init of IP block <psp> failed -22` | Disabling PSP/SMU with `ip_block_mask=0x67` is cosim policy; do not change the mask or reload the module in place |
| 4.3 | many `GART translation ... not found` messages, PM4 opcode 0, KIQ timeout | Use the GART/GPUVM playbook; interpret shared-VRAM PTE fallback only with current source provenance |
| 4.4 | GART misses, infinite DMA retry, or gem5 crash after `hipMalloc OK` | address-zero/`mapped to sink` is semantic loss and cannot support a correctness PASS |
| 4.5 | `sdma v4_4_2: ring 0 test failed (-110)` | Use the SDMA playbook; do not first change delay or expand the timeout |
| 4.6 | a VRAM address produces many GART misses, OOM, or a segfault | Use the GART/VRAM routing playbook; verify PM4/SDMA address domains and do not treat a sink as a fix |
| 4.7 | zero PTEs and a continuing PM4 opcode-0 loop | The current configuration mirrors the Q35 low/high-memory split; first check binary provenance and shared-memory layout |
| 4.8 | `curTick()` overflow or `schedule()` assertion after a long run | Historical signature only; current evidence does not prove the old causal claim, so classify the new panic artifact afresh |
| 4.9 | `Unimplemented PM4ReleaseMem.dataSelect` | Use the PM4 playbook; warn/no-op behavior for an unknown mode is incomplete semantics |
| 4.10 | `PM4 packet opcode 0x... not supported` | Use the PM4 playbook; skip semantics for `ACQUIRE_MEM`/`SET_RESOURCES` do not prove hardware fidelity |
| 4.11 | PCI class `0x0380`, driver skips the legacy VGA ROM | The current model uses `0x0300`; recurrence first indicates wrong binary/config provenance |
| 4.12 | no `ttyS0` output when combining `-serial` and `-nographic` | Launcher/runner now own a controlled console/FIFO; do not bypass wrappers with raw QEMU or `screen` commands |
| 4.13 | the gem5 linker is killed by the OOM killer | Use the build-resource playbook and lower parallelism only through `cosim_build.sh` |
| 4.14 | `Failed to init DRM client: -13`, panic in `ttm_resource_move_to_lru_tail` | Use the Guest/Driver playbook; verify locks, image metadata, and DKMS contents before attributing `-13` to one module |
| 4.15 | `rmmod amdgpu` oops in `kgd2kfd_device_exit` after partial `hw_init` | Preserve evidence, use the exact manifest only after the owning launcher process group is confirmed stopped, and start a fresh session; never unload/reload inside the contaminated Guest |

### 6.4 Issue-level playbooks

#### Guest PCI, ROM, and Driver initialization

- Trigger: 4.1, 4.2, 4.11, 4.14, 4.15, or a PCI function without a complete amdgpu/KFD/ROCm path.
- Current contract: ROM, discovery, and module parameters are Guest build/setup policy; acceptance comes only from a fresh session.
- Capture: Guest build metadata, setup-service log, PCI ID/class/BARs, binding, module state, `/dev/kfd`, render nodes, `rocminfo`, and the first kernel failure.
- Safe action: preserve the artifact, classify the first failing component, fix pinned build inputs or the setup wrapper, and cold-build only if Guest content changed.
- Forbidden: writing `/dev/mem` to manufacture a PASS, `rmmod/modprobe` after partial `hw_init`, temporary raw-image edits, or moving branches.
- Done: the same probe and a HIP acceptance row pass in fresh sessions with verified cleanup.

#### GART, GPUVM, and VRAM routing

- Trigger: 4.3, 4.4, 4.6, 4.7, a `User translation fault`, missing PTE, or VMID/PASID mismatch.
- Current contract: VRAM, VMID0 GART, and user GPUVM are separate domains; shared-backstore fallback is `[COSIM]`, not physical-GPU behavior.
- Capture: VA/PA/PTE, GART/page-table/framebuffer bases, VMID/PASID, queue/doorbell owner, packet ID, and the nearest provenance-matched PASS row.
- Safe action: use `AMDGPUDevice,GPUTLB,GPUPTWalker` to isolate the first bad translation and verify the same target after a fix.
- Forbidden: treating address-zero/`mapped to sink` as safe success, hiding misses, or dumping an unbounded page table.
- Done: the target object has a correct non-sink translation and dispatch, completion, and result checks pass.

#### SDMA initialization or ring timeout

- Trigger: 4.5 or no rptr/fence/trap progress after an SDMA doorbell.
- Current contract: current source has `sdma_delay=1000`; the old timing cause does not automatically apply to a new failure.
- Capture: queue type, rptr/wptr, doorbell, opcode, source/destination address domains, fence/trap, and a progress window.
- Safe action: use `SDMAEngine,SDMAData,AMDGPUDevice` to find the first stopped object and separate a functional stall from wall-clock budget.
- Forbidden: changing delay/keepalive first, unbounded timeout growth, or treating a later QEMU exit as primary.
- Done: the same SDMA target completes with correct fence/result and the fresh-session row passes.

#### Unsupported or partial PM4 semantics

- Trigger: 4.9, 4.10, an unknown opcode/dataSelect, or rptr stopped on one packet.
- Current contract: some packets implement only baseline-required semantics; warn-and-skip/no-op is not proof of real-AMD equivalence.
- Capture: opcode, header/count, queue/rptr/wptr, VMID/PASID, destination, release/fence/completion, and caller.
- Safe action: follow `PM4PacketProcessor::process`/`decodeHeader` to the handler and verify a minimal packet behavior test plus the same HIP row.
- Forbidden: making every unknown packet a NOP, removing only the panic, or describing a cosim workaround as hardware semantics.
- Done: required side effects, ordering, completion, and final workload result all have passing evidence.

#### gem5 build resource exhaustion

- Trigger: 4.13, an OOM kill, or linker exit after source compilation.
- Current contract: `scripts/cosim_build.sh` is the only build entry and preserves the incremental tree and provenance.
- Capture: build artifact, host memory/swap, failed stage, wrapper action/fingerprint, and first linker error.
- Safe action: rerun build preflight, then retry the same action with `GEM5_BUILD_JOBS=1 ./scripts/cosim_build.sh gem5`.
- Forbidden: raw SCons, an unrecorded linker switch, deleting the build tree, or marking a failed build up-to-date.
- Done: wrapper status passes, binary hash/provenance updates, and affected runtime rows pass.

## 7. Debug flag map

Choose the smallest flag set that fills the missing evidence dimension:

| Question | Suggested flags | Look for |
|---|---|---|
| vfio-user/BAR/IRQ transport | `MI300XCosim` | listener/client, MMIO/doorbell callback, IRQ raise |
| device routing, GART, VMID/PASID, IH | `AMDGPUDevice` | aperture setup, doorbell owner, translation, IH cookie/write pointer |
| PM4 management rings | `PM4PacketProcessor` | `MAP_PROCESS`, `MAP_QUEUES`, runlist, rptr/wptr, release/wait |
| SDMA | `SDMAEngine` | queue setup, opcode, source/destination classification, fence/trap |
| AQL queue | `HSAPacketProcessor` | doorbell, read/write/dispatch pointers, packet type and completion |
| kernel setup and signal | `GPUCommandProc,GPUKernelInfo` | code object, kernarg, Task ID, signal read/write |
| workgroup dispatch | `GPUDisp` | launch, CU assignment and workgroup completion |
| GPU virtual translation | `GPUTLB,GPUPTWalker` | VMID, walk levels, PTE and fault |
| CU/compute memory pipeline | `GPUMem` | wavefront scalar/global/local memory instructions, lane requests, retry, and completion |
| AMDGPU device memory manager | `AMDGPUMem` | chunked PM4/SDMA/device read/write requests, addresses, request IDs, retries, and callbacks |
| Ruby DMA and backpressure | `RubyDma,RubyResourceStalls` | DMA requests, retry/backpressure, and protocol completion |

`GPUMem` and `AMDGPUMem` are not spelling variants of one flag. The former is
defined under `gem5/src/gpu-compute/` and traces the CU memory pipeline. The
latter is defined in `gem5/src/dev/amdgpu/memory_manager.cc` and traces chunked
DMA reads/writes issued by AMDGPU device and packet engines. Select the one
that owns the first missing object; enable both only when evidence crosses the
two paths.

Examples still go through the fresh-session runner. This debug example omits
`COSIM_STRICT_ACCEPTANCE=1` deliberately: it is a diagnostic run, and a dirty
replay is allowed without requiring a clean HEAD.

```bash
RUN_ID="gart-debug-$(date +%Y%m%d-%H%M%S)"
COSIM_RUN_ID="$RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/tests/${RUN_ID}" \
    --gem5-debug AMDGPUDevice,GPUTLB,GPUPTWalker \
    vector_add
```

Broad flags can create huge logs and obscure the first object that differs.
Expand only after the current artifact identifies a missing dimension.

## 8. Subsystem checkpoints

### 8.1 PCI and Driver

Expected evidence is `1002:74a0`, BAR0 16 GiB, BAR2 2 MiB, BAR4 8 KiB, BAR5
512 KiB, amdgpu bound, `/dev/kfd`, at least one render node, a `gfx942` agent,
and service success. The Guest's 320-CU discovery topology is not the modeled
40-CU count. Missing ROM/discovery or forbidden IP blocks is a Guest setup
contract issue before it is a PM4/HIP issue.

### 8.2 GART and GPUVM

Capture the faulting GPU VA, resulting PA if any, PTE, GART/page-table and
framebuffer bases, VMID/PASID, queue, doorbell and last PM4/SDMA/AQL packet.
Treat `mapped to sink` or a timing-walk address-zero fallback as semantic loss,
even if the test later passes. Compare interrupt `0` and `1` only after source
and binary provenance match.

### 8.3 Queue, PM4, and SDMA

For PM4 use the actual entry `PM4PacketProcessor::process`; for AQL use
`HSAPacketProcessor::processPkt`. Record rptr/wptr/dispatch pointer and packet
type. For SDMA, distinguish gfx/page/RLC queue, opcode, VMID and raw VRAM versus
translated system address. A fence writes completion data; a trap requests IH.

### 8.4 Dispatch and completion

A credible dispatch chain contains one AQL identity, kernel launch, expected
grid/workgroup shape, progress through all required workgroups, HSA completion,
signal update and final result. In interrupt mode additionally require IH cookie,
IH write-pointer update and vfio-user MSI-X. If dispatch/completion counters
continue moving at timeout, measure scale before changing semantics.

## 9. Static and documentation gates

Run the repository contracts after script or documentation changes:

```bash
bash -n scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh \
    tests/scripts/*.sh
shellcheck scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh \
    tests/scripts/*.sh
bash tests/scripts/test_guest_disk_overlay_contract.sh
bash tests/scripts/test_guest_env_contract.sh
bash tests/scripts/test_runner_contract.sh
bash tests/scripts/test_cleanup.sh
python3 -B -m unittest discover -s tests/unit -v
python3 -B scripts/test_docs_contract.py
git diff --check
```

The documentation contract checks language pairing, first-line links, local
Markdown targets, Lab structure and three-layer boundaries, historical issue
playbooks, public wrapper commands, and critical agent routing. Generated
builds, logs, and `artifacts/` must not enter a commit.

## 10. Known workarounds and limits

- The ROM/discovery service and amdgpu module parameters are cosim boot policy,
  not physical-GPU guidance.
- PSP, SMU, RAS, DPM, audio and media blocks are disabled or omitted.
- Driver-visible topology (320 CUs/eight partitions in the measured Guest) is
  not the default 40-CU gem5 configuration.
- PM4/SDMA packet coverage, firmware scheduling, IH sources, cache maintenance,
  atomics and GPU page-fault recovery are partial.
- Shared-memory PTE reads, invalidate ACK shortcuts, low-VMID clamp and
  address-zero translation fallback are cosim-specific. The address-zero path
  is dangerous, not a safe sink.
- BAR4 exposes 256 MSI-X vectors, while the current bridge posts vector 0.
- Device-side `printf`, full multi-GPU/xGMI coherence and calibrated MI300X
  performance are outside the accepted baseline.
- The strict runner rejects `--gem5-bin` outside the current `gem5/` source
  tree so an alternate-worktree binary cannot be mixed with this tree's Python
  config/commit; this is an explicit provenance boundary.
- Strict v2 is local single-user acceptance, not cryptographic attestation.
  Coordinated rewriting of all Guest build/runtime evidence by the same UID,
  Docker/root-equivalent access, or a host administrator is outside its
  guarantees.
- A new source, Guest, QEMU, gem5 binary, environment mode or launcher contract
  requires new artifacts. Do not relabel an old PASS as current validation.
