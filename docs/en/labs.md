[中文](../zh/labs.md)

# AMD GPU Driver and Architecture Labs

These labs turn the working MI300X co-simulation baseline into a source-guided
learning environment. Run all commands from the repository root. Complete the
labs in order: later labs assume that the evidence chain from earlier labs is
already understood.

## Learning order

1. **PCI / BAR / MMIO** — establish how Linux discovers the endpoint and how
   register accesses cross the QEMU-gem5 boundary.
2. **amdgpu / KFD initialization** — follow the real Guest driver from probe to
   DRM, KFD, and ROCm agent creation.
3. **VRAM / GTT / GART / GPUVM** — separate memory domains from address
   translation and page-table state.
4. **Ring / Queue / Doorbell** — distinguish kernel PM4 management queues from
   user AQL compute queues.
5. **PM4** — trace queue-management and synchronization packets and identify
   partial model semantics.
6. **SDMA** — trace copy packets, address translation, fences, and traps.
7. **Fence / IH / MSI-X** — compare polling completion with the interrupt path.
8. **HIP end-to-end dispatch and gem5 debug** — join the entire chain and use a
   repeatable first-failure workflow.

## Reading and running the labs

### Layer labels

- `[REAL AMD]` describes behavior or source in the real Linux amdgpu/KFD or
  ROCm stack. The matching Guest package is pinned by
  `configs/cosim/guest.lock`, but its complete driver source is not vendored in
  this repository. Real-driver references therefore use canonical source paths
  and function names without invented local line numbers.
- `[GEM5]` identifies behavior implemented by the GPU model in the `gem5/`
  submodule.
- `[COSIM]` identifies the QEMU/KVM + vfio-user integration, shared-memory
  transport, launch policy, or a compatibility workaround. It is not evidence
  that physical MI300X hardware behaves the same way.

### Matching the pinned real source

Before following a `[REAL AMD]` source anchor, record the package identities
from the host lock file:

```bash
awk -F= '$1 == "AMDGPU_DKMS_VERSION" || $1 == "ROCM_VERSION" {print}' \
    configs/cosim/guest.lock
```

Then, in a diagnostic Guest session, record the installed identities and locate
the DKMS source tree:

```bash
dkms status
dpkg-query -W -f='${binary:Package}\t${Version}\n' \
    amdgpu-dkms rocm 'hsa-rocr*'
find /usr/src -mindepth 1 -maxdepth 1 -type d -name 'amdgpu-*' -print
```

Select the single amdgpu tree matching the installed `amdgpu-dkms` entry, then
record a content fingerprint before reading or changing it:

```bash
AMDGPU_DKMS_SOURCE_VERSION="$(
    dkms status | sed -n 's#^amdgpu/\([^,]*\),.*#\1#p' | sort -u
)"
test -n "$AMDGPU_DKMS_SOURCE_VERSION"
test "$(printf '%s\n' "$AMDGPU_DKMS_SOURCE_VERSION" | wc -l)" -eq 1
AMDGPU_SRC="/usr/src/amdgpu-${AMDGPU_DKMS_SOURCE_VERSION}"
test -d "$AMDGPU_SRC/drivers/gpu/drm/amd"
(
    cd "$AMDGPU_SRC"
    find drivers/gpu/drm/amd -type f -print0 | sort -z | \
        xargs -0 sha256sum | sha256sum
)
```

Archive the command output with the lab evidence. ROCr source is not installed
as a repository by the binary package: derive the release from the pinned
`ROCM_VERSION`, inspect that release's `ROCm/ROCm` `default.xml` manifest, and
use the exact ROCr component repository and immutable revision recorded there.
Record the repository URL, revision, and a checkout/archive SHA-256. Never use a
moving `develop` or `master` branch as evidence for the pinned Guest. Run these
inspection commands through the repository Guest-console workflow in a
diagnostic session; do not turn a preserved interactive session into an
acceptance row.

### Evidence baseline

The following evidence was recorded on 2026-08-23. Paths are repository-local,
under `artifacts/amd-gpu-learning-env/tests/`:

| Run | Authoritative result | Key observation |
|---|---|---|
| `phase3-driver-002` | `phase3-verdict.json`: `PASS`, reason `driver_rocm_probe_pass` | BAR0 16 GiB, BAR2 2 MiB, BAR4 8 KiB, BAR5 512 KiB; amdgpu bound; `/dev/kfd`, render nodes, and `gfx942` present |
| `phase4-baseline-vector-add-i0` | `verdict.json` and `dispatch-verdict.json`: `PASS` | polling mode; Task 2; grid 4352; workgroups 0–16; HSA and kernel completion |
| `phase4-interrupt-vector-add-i1` | `verdict.json` and `interrupt-verdict.json`: `PASS` | signal 1→0, IH cookie, IH write pointer, then vfio-user IRQ vector 0 at the same gem5 tick |

The generic artifact auditor reports `phase3-driver-002` as lacking a program
identity because Phase 3 is a driver probe rather than an operator test. Its
stage-specific `phase3-verdict.json` is the authoritative verdict for that run.
The Phase 4 rows are complete operator artifacts and share gem5 source commit
`4c1f90498f89e15a3797cb50e9b534164bc57536` and binary SHA-256
`a395b7efdaef1067223bf1e3d82780f0bdde190bee99735b12e10c377e1777a1`.
They were collected before the current qcow2-overlay hardening. They remain
valid dispatch and interrupt mechanism evidence for that recorded source and
binary, but they do not prove the current launcher's disk-isolation contract.

`artifacts/` is ignored by `.gitignore`. These logs and verdicts remain local
evidence and are not committed to Git. Copy or archive them explicitly before
removing a workspace.

### Model boundary and known limitations

Keep these constraints visible in every experiment:

- `[COSIM]` The PCI function is a synthetic vfio-user endpoint, not a physical
  MI300X PCIe endpoint. Reset, power, firmware, RAS, and error behavior are not
  hardware-equivalent.
- `[GEM5]` The measured baseline models 40 compute units and 16 GiB VRAM. The
  real Guest driver reports `active_cu_number 320` from its discovery topology;
  320 is driver-visible topology, not the number of instantiated gem5 CUs.
- `[COSIM]` PSP, SMU, RAS, DPM, audio, VCN, and JPEG are disabled or omitted for
  this path. The driver is loaded with `ip_block_mask=0x67 ppfeaturemask=0
  dpm=0 audio=0 ras_enable=0 discovery=2`.
- `[GEM5]` PM4, cache maintenance, and QEMU↔gem5 memory coherence are partial.
  In particular, `ACQUIRE_MEM` and `SET_RESOURCES` do not have full hardware
  semantics, and shared memory does not provide a complete coherence protocol.
- `[COSIM]` A missing GART PTE can be redirected to physical address zero. This
  is a dangerous keep-alive workaround: it can lose data or touch Guest RAM and
  must never be described as a safe hardware sink.
- `[COSIM]` CP_EOP interrupt cookies clamp low user VMIDs into the driver's
  compute-VMID range. Only a small subset of real IH sources is modeled.

### Supported command pattern

Every run below uses the repository wrappers. Commands that produce acceptance
rows explicitly set `COSIM_STRICT_ACCEPTANCE=1` and require clean top-level and
gem5 source trees. Only artifacts that record that value may enter the final
`cosim-matrix-verification/v2` matrix. With the variable unset or `0`, the
runner defaults to diagnostic mode: ordinary learning and dirty replay are
allowed without a clean HEAD, but their artifacts are not acceptance rows.
Each output directory must be new and empty.

```bash
LAB_RUN_ID="lab-example-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug MI300XCosim vector_add
```

Do not replace these wrappers with ad hoc Docker, QEMU, SCons, socket, or
`/dev/shm` commands. The runner records program identity, effective Guest
environment, `runner-invocation.txt`, `launch-invocation.txt`, `guest-run.sh`,
source snapshot, binary provenance, raw logs, verdict, matrix row, and scoped
cleanup result.

### Standard acceptance artifacts

Every new accepted lab row must have been launched with
`COSIM_STRICT_ACCEPTANCE=1` and must preserve `verdict.json`, `matrix.tsv`,
`runner-invocation.txt`, `launch-invocation.txt`, `guest-run.sh`, complete
`gem5.log` and `qemu.log`, source and binary provenance, `guest-overlay.json`,
`guest-base-stat.txt`, `guest-build-meta.txt`, and `cleanup-status.txt`. The
overlay must be qcow2, its backing file must be the expected raw Guest image,
and cleanup must be verified; a set of accepted leaf rows must also pass
`cosim-matrix-verification/v2`.

For a lab or regression group, compute SHA-256 of
`gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70` before the
first row and again after the final cleanup. Preserve both hash records and
require exact equality. A PASS operator result without the per-row overlay
metadata and this raw-base before/after invariant is not a disk-isolation PASS.

<a id="lab-pci-bar-mmio"></a>

## Lab 1: PCI / BAR / MMIO

### Principle

PCI enumeration establishes identity and address windows before amdgpu can
touch the device. BAR0 exposes VRAM, BAR2 carries doorbells, BAR4 contains the
MSI-X table/PBA, and BAR5 carries MMIO registers. MMIO is control traffic;
VRAM accesses and doorbell writes take different paths.

### Layer boundaries

- `[REAL AMD]` Linux enumerates a PCI display function, allocates BARs, binds
  amdgpu, and uses BAR5 register reads/writes during initialization.
- `[GEM5]` `AMDGPUDevice` implements the modeled registers and dispatches MMIO,
  doorbell, and frame-buffer accesses.
- `[COSIM]` `MI300XVfioUser` synthesizes PCI config space and BAR regions for
  QEMU's stock `vfio-user-pci` client. The measured BAR layout is a property of
  this endpoint and configuration.

### Data flow

```text
Guest PCI enumeration
  -> QEMU vfio-user-pci
  -> MI300XVfioUser config/BAR callback
  -> AMDGPUDevice MMIO, doorbell, or VRAM path
  -> modeled GPU block
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c`:
  `amdgpu_pci_probe`; `drivers/gpu/drm/amd/amdgpu/amdgpu_device.c`:
  `amdgpu_device_init`. Verify these anchors against the driver source matching
  `AMDGPU_DKMS_VERSION` in `configs/cosim/guest.lock`.
- `[GEM5]` `gem5/src/dev/amdgpu/amdgpu_device.cc`:
  `AMDGPUDevice::readMMIO`, `writeMMIO`, `writeDoorbell`, and `writeFrame`.
- `[COSIM]` `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`:
  `MI300XVfioUser::initVfuContext`, `setupBars`, `handleMmioAccess`, and
  `handleDoorbellAccess`.

### How to run

```bash
LAB_RUN_ID="lab01-pci-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug MI300XCosim,AMDGPUDevice vector_add
```

Use the fresh runner result for regression safety, then compare its device
startup with the preserved `phase3-driver-002` probe.

### Debugging

- `MI300XCosim` shows vfio-user connection, BAR callbacks, and transport events.
- `AMDGPUDevice` shows modeled MMIO and doorbell routing.
- Start at the first missing boundary: PCI device, expected BAR, vfio client
  connection, then the corresponding `AMDGPUDevice` access. A later QEMU socket
  error can be secondary to an earlier gem5 failure.

### Expected behavior

The recorded Phase 3 probe reports BAR0 `[size=16G]`, BAR2 `[size=2M]`, BAR4
`[size=8K]`, BAR5 `[size=512K]`, MSI-X enabled with 256 vectors, and amdgpu bound.
The fresh operator run must still end with exactly one `[PASS] vector_add` and a
PASS verdict.

### Experiments

- Change `--vram-size` in a disposable run and observe the BAR0 aperture; restore
  16 GiB before comparing with the baseline.
- Add passive tracing around one known BAR5 register or one doorbell offset.
  Do not write arbitrary MMIO values from the Guest.
- Compare an MMIO access, a doorbell write, and a VRAM access to demonstrate
  that they do not share one transport path.

### Acceptance artifacts

Require the standard acceptance artifacts above, including `preflight.json`,
`patch/source-snapshot.txt`, and `patch/binary-provenance.txt`. The reference BAR
evidence is
`phase3-driver-002/guest-probe-output.txt:20-23,39-41`; its authoritative result
is `phase3-driver-002/phase3-verdict.json`.

### Recovery

The runner normally performs scoped cleanup. For an interrupted runner, follow
the [run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition. Never use a broad kill or delete generic sockets, containers,
or `/dev/shm` names manually.

<a id="lab-amdgpu-kfd-init"></a>

## Lab 2: amdgpu / KFD initialization

### Principle

amdgpu initializes DRM and GPU IP blocks; KFD exposes compute queues and memory
management to ROCm. Seeing a PCI function is insufficient: the usable baseline
requires amdgpu binding, `/dev/kfd`, a DRM render node, and a `gfx942` HSA agent.

### Layer boundaries

- `[REAL AMD]` The Guest runs the pinned AMD DKMS driver and ROCm userspace.
  Probe, IP-block bring-up, DRM, KFD, and ROCm enumeration are real software
  paths operating against a modeled device.
- `[GEM5]` The model supplies ROM/register responses and the modeled GMC, IH,
  GFX, and SDMA behavior needed by those paths.
- `[COSIM]` A boot service injects the ROM, links discovery firmware, and loads
  amdgpu with unsupported blocks disabled. Physical MI300X normally has PSP,
  SMU, power, firmware, and RAS behavior that this environment omits.

### Data flow

```text
PCI probe -> ROM and IP discovery -> amdgpu_device_init
  -> enabled IP blocks -> DRM nodes -> KFD node/topology
  -> ROCr enumeration -> gfx942 HSA agent
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c`:
  `amdgpu_pci_probe`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_kms.c`:
  `amdgpu_driver_load_kms`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_device.c`:
  `amdgpu_device_init`, `amdgpu_device_ip_early_init`,
  `amdgpu_device_ip_init`, and `amdgpu_device_ip_hw_init`;
  `drivers/gpu/drm/amd/amdkfd/kfd_device.c`: `kgd2kfd_probe` and
  `kgd2kfd_device_init`.
- `[GEM5]` `gem5/src/dev/amdgpu/amdgpu_device.cc`:
  `AMDGPUDevice::readROM`, `readMMIO`, and `writeMMIO`.
- `[COSIM]`
  `gem5-resources/src/x86-ubuntu-gpu-ml/files/cosim-gpu-setup.sh` implements ROM,
  discovery, module-parameter, and node-count policy.

### How to run

```bash
LAB_RUN_ID="lab02-driver-init-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug AMDGPUDevice,PM4PacketProcessor,SDMAEngine vector_add
```

The operator run deliberately proves more than module load: it requires the
initialized driver and ROCm stack to execute a HIP kernel correctly.

Use a separate diagnostic session to capture Driver/ROCm details for this run.
`--keep-alive` is diagnostic only: because cleanup has not yet been verified,
the runner returns nonzero and its temporary verdict is not an acceptance
result. Keep `COSIM_STRICT_ACCEPTANCE` unset or `0` for this diagnostic probe;
it may replay a dirty tree and is never a final v2 matrix row. The normal
`vector_add` run above remains the operator acceptance row.

```bash
(
set -euo pipefail
PROBE_RUN_ID="${LAB_RUN_ID}-probe"
PROBE_ARTIFACT="$(realpath -m -- "artifacts/amd-gpu-learning-env/labs/${PROBE_RUN_ID}")"
SESSION_NAME="qemu-cosim-tests"
CONTROL_DIR="/tmp/${SESSION_NAME}-${PROBE_RUN_ID}.session"
CONSOLE_PIPE="${CONTROL_DIR}/console.in"
CONSOLE_LOG="${PROBE_ARTIFACT}/qemu.log"
MANIFEST="/tmp/cosim-${PROBE_RUN_ID}.session/resources.manifest"
LAUNCH_PID_FILE="${CONTROL_DIR}/launcher.pid"
LAB_CLEANUP_STATUS="${PROBE_ARTIFACT}/lab02-cleanup-status.txt"
mkdir -p "$PROBE_ARTIFACT"

# shellcheck disable=SC2317  # This function is invoked by the EXIT trap.
cleanup_lab02_probe() {
    local original_rc="$1"
    local cleanup_rc=0
    local end_line
    local launcher_pid=""
    local launcher_pgid=""
    local launcher_cmd=""
    local group_state=2
    local launcher_stopped=0
    local cleanup_proven=0
    local fallback_used=0

    trap - EXIT
    set +e
    launcher_group_alive() {
        local group_rows
        group_rows="$(ps -eo pgid=)" || return 2
        awk -v wanted="$launcher_pid" '
            $1 == wanted { found = 1 }
            END { exit(found ? 0 : 1) }
        ' <<< "$group_rows"
    }
    if [[ -f "$CONSOLE_LOG" && "${START_LINE:-}" =~ ^[0-9]+$ ]]; then
        end_line="$(wc -l < "$CONSOLE_LOG")"
        sed -n "$((START_LINE + 1)),${end_line}p" "$CONSOLE_LOG" > \
            "$PROBE_ARTIFACT/driver-rocm-probe.txt" || cleanup_rc=1
        if [[ "$original_rc" -eq 0 ]]; then
            tr -d '\r' < "$PROBE_ARTIFACT/driver-rocm-probe.txt" | \
                grep -q '^__LAB02_DRIVER_PROBE__:0$' || cleanup_rc=1
        fi
    else
        cleanup_rc=1
    fi

    if [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]]; then
        cp -- "$MANIFEST" "$PROBE_ARTIFACT/resources.manifest.snapshot" || \
            cleanup_rc=1
    else
        echo "Lab 2 exact resource manifest is missing; refusing unscoped cleanup" >&2
        cleanup_rc=1
    fi

    if [[ -d "$CONTROL_DIR" && ! -L "$CONTROL_DIR" &&
          -f "$LAUNCH_PID_FILE" && ! -L "$LAUNCH_PID_FILE" ]]; then
        read -r launcher_pid < "$LAUNCH_PID_FILE" || cleanup_rc=1
    else
        echo "Lab 2 has no trusted launcher PID file" >&2
        cleanup_rc=1
    fi

    if [[ "$launcher_pid" =~ ^[0-9]+$ ]]; then
        if [[ -d "/proc/${launcher_pid}" ]]; then
            launcher_pgid="$(ps -o pgid= -p "$launcher_pid" 2>/dev/null | tr -d ' ')"
            if [[ -r "/proc/${launcher_pid}/cmdline" ]]; then
                launcher_cmd="$(tr '\0' ' ' < "/proc/${launcher_pid}/cmdline")"
            fi
            if [[ "$launcher_pgid" == "$launcher_pid" &&
                  "$launcher_cmd" == *"scripts/cosim_launch.sh"* &&
                  "$launcher_cmd" == *"--artifact-dir ${PROBE_ARTIFACT}"* ]]; then
                kill -TERM -- "-${launcher_pid}" 2>/dev/null || true
                for _ in {1..15}; do
                    launcher_group_alive || break
                    sleep 1
                done
                if launcher_group_alive; then
                    kill -KILL -- "-${launcher_pid}" 2>/dev/null || true
                    for _ in {1..5}; do
                        launcher_group_alive || break
                        sleep 1
                    done
                fi
            else
                echo "Lab 2 refuses to stop a process group not proven to own this run" >&2
                cleanup_rc=1
            fi
        fi
        if launcher_group_alive; then
            echo "Lab 2 launcher process group is still live; refusing concurrent manifest removal" >&2
            cleanup_rc=1
        else
            group_state=$?
            if [[ "$group_state" -eq 1 ]]; then
                launcher_stopped=1
            else
                echo "Lab 2 cannot prove launcher process group exit" >&2
                cleanup_rc=1
            fi
        fi
    else
        echo "Lab 2 launcher PID is invalid" >&2
        cleanup_rc=1
    fi

    if [[ "$launcher_stopped" -eq 1 ]]; then
        if grep -qx 'result=PASS' \
                "$PROBE_ARTIFACT/cleanup-status.txt" 2>/dev/null; then
            cleanup_proven=1
        elif [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]]; then
            fallback_used=1
            if ./scripts/cosim_cleanup.sh --run-id "$PROBE_RUN_ID" \
                    --manifest "$MANIFEST" --confirm > \
                    "$PROBE_ARTIFACT/manifest-cleanup.log" 2>&1; then
                cleanup_proven=1
            else
                cleanup_rc=1
            fi
        fi
    fi

    [[ "$cleanup_proven" -eq 1 ]] || cleanup_rc=1
    [[ ! -e "$MANIFEST" && ! -L "$MANIFEST" ]] || cleanup_rc=1

    if [[ -L "$CONTROL_DIR" ]]; then
        cleanup_rc=1
    elif [[ -d "$CONTROL_DIR" ]]; then
        rm -f -- "$CONSOLE_PIPE" "$LAUNCH_PID_FILE" || cleanup_rc=1
        rmdir -- "$CONTROL_DIR" || cleanup_rc=1
    fi

    {
        if [[ "$cleanup_rc" -eq 0 ]]; then
            echo 'result=PASS'
        else
            echo 'result=FAIL'
        fi
        echo "probe_exit_code=${original_rc}"
        echo "launcher_stopped=${launcher_stopped}"
        echo "fallback_cleanup=${fallback_used}"
    } > "$LAB_CLEANUP_STATUS"

    if [[ "$original_rc" -ne 0 ]]; then
        exit "$original_rc"
    fi
    exit "$cleanup_rc"
}
trap 'cleanup_lab02_probe $?' EXIT

set +e
COSIM_RUN_ID="$PROBE_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh --keep-alive \
    --session-name "$SESSION_NAME" \
    --output-dir "$PROBE_ARTIFACT" vector_add
KEEP_ALIVE_RC=$?
set -e
printf '%s\n' "$KEEP_ALIVE_RC" > "$PROBE_ARTIFACT/keep-alive-exit.txt"
test "$KEEP_ALIVE_RC" -ne 0
test -p "$CONSOLE_PIPE"
test -f "$CONSOLE_LOG"

START_LINE="$(wc -l < "$CONSOLE_LOG")"
# shellcheck disable=SC2016  # These variables must expand in the Guest.
GUEST_PROBE_COMMAND='{
    lspci -nnk -d 1002:74a0
    lsmod | grep "^amdgpu "
    ls -l /dev/kfd /dev/dri/renderD*
    rocminfo
    rocm-smi
    dmesg | grep -iE "amdgpu|kfd" | tail -n 120
    lspci -nnk -d 1002:74a0 | grep -q "Kernel driver in use: amdgpu" &&
        lsmod | grep -q "^amdgpu " &&
        test -c /dev/kfd &&
        compgen -G "/dev/dri/renderD*" >/dev/null &&
        rocminfo 2>/dev/null | grep -q gfx942 &&
        rocm-smi >/dev/null
}; rc=$?
echo __LAB02_DRIVER_PROBE__:${rc}'
printf '%s\n' "$GUEST_PROBE_COMMAND" > "$CONSOLE_PIPE"

DEADLINE=$((SECONDS + 120))
while ! tail -n +"$((START_LINE + 1))" "$CONSOLE_LOG" | tr -d '\r' | \
    grep -q '^__LAB02_DRIVER_PROBE__:[0-9][0-9]*$'; do
    (( SECONDS < DEADLINE )) || {
        echo "Lab 2 Driver/ROCm probe timed out" >&2
        exit 1
    }
    sleep 2
done
tail -n +"$((START_LINE + 1))" "$CONSOLE_LOG" | tr -d '\r' | \
    grep -q '^__LAB02_DRIVER_PROBE__:0$'
exit 0
)
```

The diagnostic commands enter the Guest through its root console pipe; they do
not read or store a sudo password. After a timeout, command failure, or normal
completion, the `EXIT` trap archives the probe window and manifest snapshot,
validates and stops this run's launcher process group, and permits manifest
fallback only after that group exits. This avoids concurrent cleanup, and the
subshell keeps `exit` from closing the caller's interactive shell. The probe
must end with
`__LAB02_DRIVER_PROBE__:0` and archive PCI driver binding, the amdgpu module,
`/dev/kfd`, render nodes, complete `rocminfo`/`rocm-smi` output, and relevant
kernel logs in `driver-rocm-probe.txt`. It must also preserve
`resources.manifest.snapshot`, and `lab02-cleanup-status.txt` must contain
`result=PASS`.

### Debugging

- `AMDGPUDevice` locates the last modeled register access before a driver stall.
- `PM4PacketProcessor` and `SDMAEngine` distinguish GFX/KIQ and SDMA ring-test
  progress from a generic module-load timeout.
- In `qemu.log`, classify the first failed boundary: ROM/discovery, IP block,
  DRM, KFD, ROCm agent, compile, or operator. Do not unload amdgpu after a
  partial `hw_init` failure; cleanup and start a fresh session.

### Expected behavior

The Phase 3 evidence records amdgpu 6.14.14 bound to `1002:74a0`, `/dev/kfd`,
render nodes, `gfx942`, 16383 MiB usable VRAM, a 512 MiB GART aperture, KFD
device creation, and final `[COSIM_PHASE3_VERDICT] PASS`. Driver discovery
reports 320 active CUs, while gem5 instantiated 40 CUs for the measured run.

### Experiments

- Trace the time and first register access for each enabled IP block.
- In a disposable Guest image, change one module parameter at a time and record
  the first failing IP block. Never treat a PSP/SMU-disabled result as physical
  hardware behavior.
- Compare the driver-visible topology with the `--num-cus` model parameter.

### Acceptance artifacts

Keep both the normal runner PASS artifact and the diagnostic session's
`driver-rocm-probe.txt`, `keep-alive-exit.txt`, `qemu.log`, `gem5.log`,
`resources.manifest.snapshot`, and `lab02-cleanup-status.txt`. The probe token
must be zero, cleanup must report PASS, and the output must prove amdgpu binding,
`/dev/kfd`, at least one render node, a `gfx942` agent, and a successful
`rocm-smi`. The historical `phase3-driver-002` run is comparison evidence only;
it cannot replace evidence from the current Lab session.

### Recovery

The runner cleans run-scoped resources for the normal acceptance row. After
any partial driver initialization, discard the Guest session and start again;
do not attempt an `rmmod amdgpu` recovery. If diagnostic cleanup is interrupted,
follow the [run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.

<a id="lab-vram-gtt-gart-gpuvm"></a>

## Lab 3: VRAM / GTT / GART / GPUVM

### Principle

VRAM and GTT are allocation domains; GART and per-process GPUVM page tables are
translation structures. VMID 0 uses kernel mappings, while user queues use a
VMID/PASID context and multi-level page walks. Correct allocation without
correct translation and visibility is not a passing result.

### Layer boundaries

- `[REAL AMD]` amdgpu/KFD allocates BOs, builds mappings, updates page tables,
  and invalidates translation state.
- `[GEM5]` `AMDGPUVM`, the Vega walker/TLB, memory manager, and GPU memory
  system perform modeled translation and access.
- `[COSIM]` Guest RAM and VRAM are separate shared mappings. QEMU writes can
  bypass gem5 caches, so fallback PTE reads and explicit invalidations are
  compatibility mechanisms, not coherent hardware memory.

### Data flow

```text
HIP allocation -> KFD/amdgpu BO and GPUVA mapping
  -> VMID page-table or VMID-0 GART translation
  -> Guest RAM (GTT) or shared VRAM
  -> CP/SDMA/CU access -> completion and result copy
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_gart.c`:
  `amdgpu_gart_init` and `amdgpu_gart_bind`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_vm.c`: `amdgpu_vm_init`,
  `amdgpu_vm_bo_map`, and `amdgpu_vm_update_range`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_amdkfd_gpuvm.c`:
  `amdgpu_amdkfd_gpuvm_alloc_memory_of_gpu`.
- `[GEM5]` `gem5/src/dev/amdgpu/amdgpu_vm.cc`:
  `AMDGPUVM::writeMMIOGfx940`, `invalidateTLBs`,
  `GARTTranslationGen::translate`, `MMHUBTranslationGen::translate`, and
  `UserTranslationGen::translate`; `amdgpu_device.cc`:
  `AMDGPUDevice::writeFrame`.
- `[COSIM]` `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`:
  `MI300XVfioUser::setupSharedMemory` and DMA mapping callbacks.

### How to run

```bash
LAB_RUN_ID="lab03-gpuvm-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug AMDGPUMem,AMDGPUDevice,GPUTLB,GPUCommandProc vector_add
```

### Debugging

- `AMDGPUMem` follows modeled GPU-memory requests.
- `AMDGPUDevice` exposes GART setup, apertures, and translation warnings.
- `GPUTLB` follows user translation and invalidation; `GPUCommandProc` ties the
  translated kernarg/code/data addresses to one dispatch.
- Compare VMID, original GPUVA, PTE, translated physical address, memory domain,
  and the last invalidation. A `paddr=0` fallback is a correctness warning even
  if the process remains alive.

### Expected behavior

The reference probe reports BAR-visible 16 GiB VRAM, 16383 MiB usable VRAM,
approximately 3970 MiB GTT, and a 512 MiB enabled GART with its PTB in VRAM.
The fresh test must complete H2D copies, Task 2 dispatch, D2H copy, and exact
vector comparison without a translation fault or silent wrong result.

### Experiments

- Change `vector_add` length so buffers cross one and then several 4 KiB pages.
- Compare a fresh single run with `--repeat` runs to expose stale TLB/PWC state.
- Vary `--vram-size` only in disposable runs and record changes in aperture,
  page-table placement, and failure point.
- Instrument a single GPUVA through GART/User translation; do not “fix” a
  missing PTE by accepting the physical-zero fallback.

### Acceptance artifacts

Require the standard runner artifacts and preserve the GPUVA/PTE/VMID evidence
in `gem5.log`. The reference memory facts are in
`phase3-driver-002/guest-probe-output.txt:98-105,433-444`. Acceptance requires a
PASS verdict and correct data, not merely successful allocation or no crash.

### Recovery

Use a new runner session after every translation or coherence failure; stale
page tables and caches make an in-place retry ambiguous. Preserve the failed
artifact. If runner cleanup is interrupted, follow the
[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.

<a id="lab-ring-queue-doorbell"></a>

## Lab 4: Ring / Queue / Doorbell

### Principle

Kernel PM4 queues provision process and queue state; a user HSA queue contains
AQL packets. A doorbell advertises a new write position rather than carrying
the command itself. Queue identity, doorbell offset, VMID/PASID, and pointer
movement must agree.

### Layer boundaries

- `[REAL AMD]` amdgpu owns hardware rings and KFD creates process queues and
  maps doorbells for ROCr.
- `[GEM5]` PM4 queue handling establishes descriptors; `HWScheduler` and
  `HSAPacketProcessor` fetch and schedule AQL packets.
- `[COSIM]` BAR2 doorbell writes cross vfio-user callbacks. Doorbell routing is
  modeled in gem5 rather than occurring in physical doorbell hardware.

### Data flow

```text
KFD MAP_PROCESS / MAP_QUEUES -> VMID, PASID, MQD, doorbell mapping
ROCr writes AQL packet -> updates queue write pointer -> rings BAR2 doorbell
MI300XVfioUser -> AMDGPUDevice -> HWScheduler -> HSAPacketProcessor fetch
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_ring.c`:
  `amdgpu_ring_init`, `amdgpu_ring_alloc`, and `amdgpu_ring_commit`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_doorbell_mgr.c`;
  `drivers/gpu/drm/amd/amdkfd/kfd_chardev.c`: `kfd_ioctl_create_queue`;
  `drivers/gpu/drm/amd/amdkfd/kfd_process_queue_manager.c`:
  `pqm_create_queue`.
- `[GEM5]` `gem5/src/dev/amdgpu/pm4_packet_processor.cc`:
  `PM4PacketProcessor::mapProcess`, `mapQueues`, and `processMQD`;
  `gem5/src/dev/hsa/hw_scheduler.cc`: `HWScheduler::registerNewQueue` and
  `write`; `hsa_packet_processor.cc`: `setDeviceQueueDesc`.
- `[COSIM]` `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`:
  `MI300XVfioUser::handleDoorbellAccess` forwards to
  `gem5/src/dev/amdgpu/amdgpu_device.cc`: `AMDGPUDevice::writeDoorbell`;
  queue setup uses `mapDoorbellToVMID` before queue-type routing.

### How to run

```bash
LAB_RUN_ID="lab04-queues-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug MI300XCosim,AMDGPUDevice,PM4PacketProcessor,HSAPacketProcessor vector_add
```

### Debugging

- `MI300XCosim` and `AMDGPUDevice` identify each BAR2 write and selected queue.
- `PM4PacketProcessor` shows MAP_PROCESS/MAP_QUEUES and MQD handling.
- `HSAPacketProcessor` shows AQL queue read/write/dispatch indexes and packet
  completion.
- Build a table keyed by queue/doorbell with VMID, PASID, read pointer, write
  pointer, dispatch pointer, and final empty state. Do not infer progress from a
  doorbell alone.

### Expected behavior

The driver initializes KIQ, compute, and SDMA rings. For vector addition, an AQL
queue advances to Task 2, all 17 workgroups are fetched and dispatched, the
read pointer catches the write pointer, and the test produces one PASS marker.

### Experiments

- Change grid size to produce fewer than, equal to, and more than 40 workgroups.
- Use `--repeat` to check that queue IDs and pointers are fresh per session.
- Add passive queue/doorbell correlation fields; avoid global logging without
  queue identity.

### Acceptance artifacts

Require the standard runner evidence plus a queue table derived from raw
`gem5.log`. For comparison, the baseline AQL packet is at
`phase4-baseline-vector-add-i0/gem5.log:1450`, first workgroup at `:1478`, last
at `:1718`, and queue completion at `:3395-3403`.

### Recovery

A stuck queue is not safely reusable. Preserve pointer state and the first
failing packet, end the session, and let the runner clean that run. If runner
cleanup is interrupted, follow the
[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.

<a id="lab-pm4"></a>

## Lab 5: PM4

### Principle

PM4 is the kernel-level command language used here for process/queue setup,
indirect buffers, writes, waits, and completion. The HIP kernel itself arrives
as an AQL dispatch packet after PM4 has provisioned the queue; it is misleading
to describe every HIP dispatch as one PM4 dispatch packet.

### Layer boundaries

- `[REAL AMD]` KFD and ASIC ring code construct hardware PM4 packets with real
  cache, ordering, and queue semantics.
- `[GEM5]` `PM4PacketProcessor` decodes a supported subset and advances modeled
  queues.
- `[COSIM]` Some packets are skipped or approximated to keep the Guest driver
  moving. These are model limitations, not valid AMD hardware substitutions.

### Data flow

```text
KFD packet manager -> PM4 ring -> doorbell
  -> PM4PacketProcessor::process/decodeHeader
  -> MAP_PROCESS / MAP_QUEUES / RUN_LIST / IB / WRITE_DATA / RELEASE_MEM
  -> queue state, memory write, wait, or completion
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdkfd/kfd_packet_manager.c`:
  `pm_send_runlist` and `pm_send_set_resources`; canonical PM4 layouts are in
  `drivers/gpu/drm/amd/amdkfd/kfd_pm4_headers.h` and
  `drivers/gpu/drm/amd/amdkfd/kfd_pm4_opcodes.h`;
  ASIC ring emission is in `drivers/gpu/drm/amd/amdgpu/gfx_v9_4_3.c`.
- `[GEM5]` `gem5/src/dev/amdgpu/pm4_defines.hh`; and
  `pm4_packet_processor.cc`: `process`, `decodeHeader`, `mapProcess`,
  `mapQueues`, `runList`, `indirectBuffer`, `writeData`, `waitRegMem`, and
  `releaseMem`.
- `[COSIM]` `gem5/src/dev/amdgpu/pm4_packet_processor.cc`: current
  `IT_ACQUIRE_MEM` and `IT_SET_RESOURCES` handling advances the read pointer
  without full hardware semantics; unsupported opcodes warn and are skipped.

### How to run

```bash
LAB_RUN_ID="lab05-pm4-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug PM4PacketProcessor,AMDGPUDevice vector_add
```

### Debugging

- `PM4PacketProcessor` is the authoritative packet decoder flag.
- `AMDGPUDevice` adds doorbell, VMID/PASID, and destination routing context.
- Count packets by queue and opcode, then identify the first packet whose input,
  read-pointer advance, memory effect, or completion is missing. Keep unknown
  opcodes visible rather than classifying them as NOPs.

### Expected behavior

MAP_PROCESS establishes a process translation context, MAP_QUEUES consumes an
MQD, and the management sequence reaches a usable AQL queue. Supported packets
advance their read pointers without a panic. A PASS result does not prove full
ACQUIRE_MEM or cache-flush equivalence.

### Experiments

- Produce an opcode/count timeline for driver initialization and one HIP run.
- Compare WRITE_DATA and RELEASE_MEM destinations in system memory and VRAM.
- Implement or instrument one missing cache-maintenance semantic in an isolated
  gem5 branch, rebuild through `cosim_build.sh`, and rerun the same artifact
  matrix; never silently turn an unknown opcode into success.

### Acceptance artifacts

Require the runner evidence plus a packet table containing source log line,
queue, opcode, input address, output effect, and status. Acceptance means the
operator PASS and no unexplained unsupported packet on its causal path. Record
partial semantics explicitly even when the test passes.

### Recovery

Preserve the first unsupported packet and the surrounding raw log before
cleanup. Rebuild only through `./scripts/cosim_build.sh gem5`; use a fresh
runner session for the retry. If runner cleanup of an abandoned run is
interrupted, follow the
[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.

<a id="lab-sdma"></a>

## Lab 6: SDMA

### Principle

SDMA performs asynchronous copies and memory operations through its own queues.
Its packets still depend on correct VM translation, memory-domain routing,
read-pointer writeback, fences, and optional traps.

### Layer boundaries

- `[REAL AMD]` The driver initializes SDMA rings and emits ASIC packet formats;
  ROCm may use them for copies and queue operations.
- `[GEM5]` `SDMAEngine` decodes and executes the supported packet subset.
- `[COSIM]` The model uses a 1000-tick SDMA delay so ring tests finish within
  the Guest timeout, and routes shared-memory/VRAM accesses through cosim paths.
  This is not physical SDMA timing.

### Data flow

```text
hipMemcpy or driver ring test -> SDMA packet ring -> SDMA doorbell
  -> decode -> VM/GART translation -> copy/write
  -> rptr and fence -> optional TRAP/IH -> waiter
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/sdma_v4_4_2.c`:
  `sdma_v4_4_2_ring_emit_ib`, `sdma_v4_4_2_ring_emit_fence`, and
  `sdma_v4_4_2_ring_test_ring`.
- `[GEM5]` `gem5/src/dev/amdgpu/sdma_engine.cc`:
  `SDMAEngine::decodeNext`, `decodeHeader`, `translate`, `copy`, `fence`, and
  `trap`; `sdma_engine.hh` defines the cosim `sdma_delay`.
- `[COSIM]` SDMA translates Guest addresses against shared Guest RAM and routes
  device addresses to modeled/shared VRAM; some GART shadow updates compensate
  for BAR0 writes that bypass gem5.

### How to run

```bash
LAB_RUN_ID="lab06-sdma-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug SDMAEngine,SDMAData,AMDGPUMem vector_add
```

### Debugging

- `SDMAEngine` shows queue state, opcode decode, fences, and traps.
- `SDMAData` shows packet data movement; enable it only for bounded runs because
  it can be verbose.
- `AMDGPUMem` shows final modeled memory requests. Correlate source/destination,
  VMID, translated address, byte count, rptr, fence value, and data checksum.

### Expected behavior

Phase 3 records initialized SDMA rings, and the vector test completes H2D/D2H
copies with correct output. There is no SDMA ring-test `-110`, no unsupported
packet on the required path, the rptr advances, and the completion fence is
observable.

### Experiments

- Change vector length to exercise small, unaligned, and multi-page copies.
- Add a repository HIP test that separates H2D, D2H, and D2D checks, then run it
  only through `run_cosim_tests.sh`.
- Compare the 1000-tick cosim delay with a larger value in an isolated branch;
  classify timeout behavior without claiming performance equivalence.

### Acceptance artifacts

Require the standard runner artifacts plus a bounded SDMA table with queue,
opcode, source, destination, bytes, translation, fence, and result checksum.
Reference ring creation is visible in
`phase3-driver-002/guest-probe-output.txt:536-551`. Correct copied data and a
PASS verdict are mandatory.

### Recovery

If SDMA stalls, preserve queue pointers, the last decoded packet, and pending
DMA callback state. End the session and rerun fresh; never reuse a partially
advanced ring. Let the runner clean the run. If runner cleanup is interrupted,
follow the [run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.

<a id="lab-fence-ih-msix"></a>

## Lab 7: Fence / IH / MSI-X

### Principle

A memory completion value and an interrupt are distinct. Polling waits for the
signal value to change; interrupt mode additionally writes an IH entry and
write pointer, raises MSI-X, and lets KFD wake the owning process. Both paths
must be tested independently.

### Layer boundaries

- `[REAL AMD]` amdgpu fences, IH, IRQ dispatch, and KFD events implement the
  real software completion path.
- `[GEM5]` command/SDMA completion updates the HSA signal and the modeled IH
  builds a cookie and ring entry.
- `[COSIM]` vfio-user raises an eventfd-backed MSI-X vector into QEMU/KVM. The
  model clamps some VMIDs and implements only CP_EOP/TRAP IH sources.

### Data flow

```text
Polling (HSA=0): GPU completion -> signal 1->0 -> host observes value

Interrupt (HSA=1): GPU completion -> signal 1->0 -> IH cookie
  -> IH ring write -> IH wptr update -> vfio-user MSI-X vector 0
  -> Guest amdgpu/KFD waiter
```

### Source and key functions

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_fence.c`:
  `amdgpu_fence_process` and `amdgpu_fence_driver_init_ring`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_ih.c`: `amdgpu_ih_process`;
  `drivers/gpu/drm/amd/amdgpu/amdgpu_irq.c`: `amdgpu_irq_dispatch`;
  `drivers/gpu/drm/amd/amdkfd/kfd_events.c`:
  `kfd_signal_event_interrupt`.
- `[GEM5]` `gem5/src/gpu-compute/gpu_command_processor.cc`:
  `GPUCommandProcessor::updateHsaSignalData` and `sendCompletionSignal`;
  `gem5/src/dev/amdgpu/interrupt_handler.cc`:
  `AMDGPUInterruptHandler::prepareInterruptCookie`, `submitInterruptCookie`,
  `submitWritePointer`, and `intrPost`.
- `[COSIM]` `gem5/src/dev/amdgpu/amdgpu_device.cc`:
  `AMDGPUDevice::intrPost` routes cosim interrupts to
  `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`:
  `MI300XVfioUser::sendIrqRaise`, which forwards the selected vector through
  vfio-user.

### How to run

Run polling and interrupt modes as separate fresh sessions:

```bash
POLL_RUN_ID="lab07-poll-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$POLL_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${POLL_RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo vector_add

IRQ_RUN_ID="lab07-irq-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$IRQ_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${IRQ_RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,AMDGPUDevice,MI300XCosim vector_add
```

Run `cosim_preflight.sh run` before the pair if the host or build state changed.

### Debugging

- Polling flags: `HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo`.
- Interrupt flags:
  `HSAPacketProcessor,GPUCommandProc,GPUDisp,AMDGPUDevice,MI300XCosim`.
- In interrupt mode, require one ordered chain with the same signal and tick:
  signal transition, IH ring state, cookie write, write-pointer update, and IRQ
  raise. Record VMID and PASID; a vector alone does not prove delivery to the
  correct process.

### Expected behavior

Polling mode records `HSA_ENABLE_INTERRUPT=0`, completes vector addition, and
does not require MSI-X for the wait. Interrupt mode records
`HSA_ENABLE_INTERRUPT=1` and the measured chain at tick `783006015986249`:
signal `1 -> 0`, IH cookie write, IH write-pointer update, then vfio-user IRQ
vector 0. Both runs end with one PASS marker and verified cleanup.

### Experiments

- Repeat each mode in fresh sessions and compare completion/interrupt counts.
- Add a second GPU operation before `hipFree` to stress VMID/PASID routing.
- Compare CP_EOP and SDMA TRAP sources, keeping their cookies and queue identity
  separate.

### Acceptance artifacts

For both runs require `matrix.tsv` to record the effective HSA value and
`verdict.json` to PASS. The reference interrupt chain is in
`phase4-interrupt-vector-add-i1/gem5.log:229167-229179` and summarized by
`interrupt-verdict.json`. In the preserved polling `qemu.log`, locate the mode
and PASS marker by the exact patterns `[COSIM_ENV] HSA_ENABLE_INTERRUPT=0` and
`[PASS] vector_add`; they currently resolve to lines 765 and 783. Prefer these
stable patterns over hard-coded line numbers when regenerating evidence.

### Recovery

An interrupt timeout requires a fresh session after preserving signal, IH,
VMID/PASID, and vfio evidence. Do not switch HSA mode inside one live Guest and
compare the results. If runner cleanup is interrupted, follow the
[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.

<a id="lab-hip-dispatch"></a>

## Lab 8: HIP end-to-end dispatch and gem5 debug

### Principle

The final lab proves the complete functional chain rather than one subsystem.
HIP/ROCr allocates memory and creates an AQL dispatch; KFD/amdgpu establishes
the process and queues; gem5 fetches the packet, launches workgroups, executes
the kernel, updates completion, and returns data to the Guest.

### Layer boundaries

- `[REAL AMD]` The Guest HIP, ROCr, KFD, amdgpu, and ABI paths are real software
  from the pinned image, operating against the modeled device.
- `[GEM5]` HSA packet processing, command processing, dispatcher, shader, CUs,
  instruction pipeline, TLBs, and memory system model GPU execution.
- `[COSIM]` QEMU executes the CPU/driver, vfio-user carries device operations,
  and shared memory connects Guest RAM/VRAM. Passing this lab validates this
  configuration, not all physical MI300X behavior.

### Data flow

```text
HIP API -> ROCr -> KFD ioctls and mappings -> PM4 process/queue setup
  -> AQL packet in user queue -> AQL doorbell -> HWScheduler
  -> HSAPacketProcessor -> GPUCommandProcessor -> GPUDispatcher
  -> Shader -> ComputeUnit execution -> HSA completion
  -> polling or IH/MSI-X -> hipDeviceSynchronize -> D2H -> result check
```

### Source and key functions

- `[REAL AMD]` HIP/CLR `clr/hipamd/src/hip_platform.cpp`:
  `ihipLaunchKernel`; `clr/hipamd/src/hip_module.cpp`:
  `ihipLaunchKernelCommand` and `ihipModuleLaunchKernel`;
  `clr/hipamd/src/hip_stream.cpp`: `hipStreamSynchronize_common`;
  `clr/rocclr/device/rocm/rocvirtual.cpp`: `VirtualGPU::submitKernel`,
  `submitKernelInternal`, `dispatchAqlPacket`, and `dispatchGenericAqlPacket`.
- `[REAL AMD]` ROCr
  `ROCR-Runtime/runtime/hsa-runtime/core/runtime/amd_gpu_agent.cpp`:
  `GpuAgent::QueueCreate`; `amd_aql_queue.cpp`:
  `AqlQueue::AddWriteIndex*`, `StoreRelaxed`, and `StoreRelease`; `hsa.cpp`:
  `hsa_queue_create`, `hsa_queue_add_write_index_*`,
  `hsa_signal_store_screlease`, and `hsa_signal_wait_scacquire`. These anchors
  were checked against CLR `a3e329ad8a92` and ROCr `737ba1dcdfa9` from
  `rocm-7.0.0`. The Lab must still record the immutable revisions corresponding
  to the actual Guest packages through the manifest workflow above; a tag name
  alone is not binary provenance.
- `[REAL AMD]` `drivers/gpu/drm/amd/amdkfd/kfd_chardev.c`:
  `kfd_ioctl_create_queue`;
  `drivers/gpu/drm/amd/amdkfd/kfd_process_queue_manager.c`:
  `pqm_create_queue`; `drivers/gpu/drm/amd/amdgpu/amdgpu_amdkfd_gpuvm.c`:
  `amdgpu_amdkfd_gpuvm_alloc_memory_of_gpu`. The AQL ABI and ROCr sources must
  be matched to the ROCm version pinned in `configs/cosim/guest.lock`.
- `[GEM5]` `gem5/src/dev/hsa/hsa_packet_processor.cc`:
  `HSAPacketProcessor::getCommandsFromHost`, `processPkt`, and `finishPkt`;
  `gem5/src/gpu-compute/gpu_command_processor.cc`:
  `submitDispatchPkt`, `dispatchKernelObject`, `dispatchPkt`, and
  `sendCompletionSignal`; `dispatcher.cc`: `GPUDispatcher::dispatch` and
  `notifyWgCompl`; `shader.cc`: `Shader::dispatchWorkgroups`;
  `compute_unit.cc`: `ComputeUnit::dispWorkgroup`.
- `[COSIM]` `scripts/run_cosim_tests.sh` fixes program identity, embeds the
  effective HSA mode, launches a fresh session, classifies the result, and
  archives source/binary provenance and raw logs.

### How to run

First reproduce the measured polling trace:

```bash
LAB_RUN_ID="lab08-dispatch-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$LAB_RUN_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo vector_add
```

After a single PASS, run a fresh-session repetition matrix:

```bash
REPEAT_ID="lab08-repeat-$(date +%Y%m%d-%H%M%S)"
COSIM_STRICT_ACCEPTANCE=1 COSIM_RUN_ID="$REPEAT_ID" \
    GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh --repeat 3 \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${REPEAT_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo vector_add
```

<a id="lab-gem5-debug"></a>

### Debugging

- Start with the measured flags:
  `HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo`.
- Add only the dimension needed by evidence: `GPUFetch`, `GPUExec`, `GPUSched`,
  `GPUTLB`, `AMDGPUMem`, `PM4PacketProcessor`, or `SDMAEngine`.
- Classify the first absent transition: AQL fetch, kernel-object/ABI decode,
  workgroup dispatch, execution progress, completion signal, interrupt/wait, or
  D2H validation. Build compact dispatch, queue, signal, translation, and cache
  tables before reading large log regions.
- A timeout is not a root cause. Determine whether progress still changes,
  whether all workgroups completed, and whether the final signal/wait object was
  covered by the trace.

### Expected behavior

The measured polling run found a unique AQL packet for Task 2 with workgroup
size 256 and grid size 4352. It dispatched exactly 17 workgroups, WG 0 through
WG 16, recorded HSA completion and `Completed kernel 2`, then returned the
correct vector result with one `[PASS] vector_add`. Two transient invalidate
retries before launch were observed and resolved; they are not permission to
ignore a persistent cache/coherence failure.

### Experiments

- Change vector length and threads per block; predict grid/workgroup count
  before running and compare with the trace.
- Run the same binary repeatedly in fresh sessions, then add a multi-operation
  test to expose stale PWC/TLB/SQC/GL2 state.
- Compare polling and interrupt completion using Lab 7 without changing the
  program identity.
- Add one narrowly scoped debug flag at a time and report object/filter coverage
  so absence of an event remains meaningful.

### Acceptance artifacts

Require a PASS `verdict.json`, matching `matrix.tsv`, exact program source and
binary hashes, source snapshot, binary provenance, complete gem5/QEMU logs, and
verified cleanup for every row. The reference dispatch is summarized in
`phase4-baseline-vector-add-i0/dispatch-verdict.json`; raw anchors are
`gem5.log:1450` (Task/AQL), `:1478-1718` (WG 0–16), and `:3395-3403`
(completion). Artifacts stay local and are not proof unless their provenance is
preserved.

### Recovery

Preserve the complete failing row before cleanup. Route the first failing
component through the repository debug workflow, make one bounded change,
rebuild only through `cosim_build.sh`, and retry in a fresh runner session with
the same acceptance criteria. Stop after a PASS matrix with matching
provenance; never overwrite the failed artifact directory. If runner cleanup is
interrupted, follow the
[run-scoped recovery procedure](getting-started.md#manifest-scoped-cleanup):
validate `launcher.pid`, its `scripts/cosim_launch.sh` process group, and
`--artifact-dir`; stop that exact group and confirm its exit; only then may
`cosim_cleanup.sh` use the exact manifest. Missing or mismatched ownership is a
stop condition; never use a broad kill.
