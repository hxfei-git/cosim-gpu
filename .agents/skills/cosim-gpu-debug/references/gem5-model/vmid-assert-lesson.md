# VMID Assertion in PM4 mapQueues

## Cherry-Pick Gate — READ FIRST

When cherry-picking ANY commit from `fix-hsa-signal-ih-completion-2`
to another branch, `assert(queue_vmid)` WILL be introduced and WILL
crash cosim during GPU init. Check immediately after cherry-pick:

```bash
grep -n 'assert.*vmid' src/dev/amdgpu/pm4_packet_processor.cc
```

If line 532 is `assert(queue_vmid);`, replace with:

```cpp
const uint16_t queue_vmid = pkt->vmid ? pkt->vmid : gpuDevice->lastVMID();
```

See ## Fix below for details.

## Discovery

June 2026, cosim launch crash investigation. Guest amdgpu driver loaded
successfully but gem5 crashed during PM4 MAP_QUEUES packet processing
with `assert(queue_vmid)` at `pm4_packet_processor.cc:532`.

The assertion was introduced by commit `aa5d08d97773` on branch
`fix-hsa-signal-ih-completion-2`, which threads VMID through the signal
completion chain for multi-process HSA support.

## Evidence

### Crash chain

```
amdgpu driver sends MAP_QUEUES PM4 packet with vmid=0
  → PM4PacketProcessor::mapQueues(pkt)
    → const uint16_t queue_vmid = pkt->vmid;  // = 0
    → assert(queue_vmid);                      // FAILS
  → gem5 dies
  → vfio-user socket closes
  → QEMU vfio_user_recv_hdr sees ECONNRESET
  → qio_channel_readv_full sets *errp
  → error_setg_errno sets *errp again → assert(*errp == NULL) fails → SIGABRT
```

### Before the assertion (working code, commit `8680505b71`):

```cpp
// pm4_packet_processor.cc:mapQueues, before VMID threading:
gpuDevice->mapDoorbellToVMID(pkt->doorbellOffset << 2,
                             gpuDevice->lastVMID());
```

### After the assertion (broken, commit `aa5d08d97773`):

```cpp
// pm4_packet_processor.cc:532:
const uint16_t queue_vmid = pkt->vmid;
assert(queue_vmid);
//
gpuDevice->mapDoorbellToVMID(pkt->doorbellOffset << 2, queue_vmid);
```

### Branches affected

| Branch | Commit | Has assert? | Status |
|--------|--------|-------------|--------|
| `fix-hsa-signal-only` | `8680505b71` | No | Works |
| `fix-hsa-signal-ih-completion-2` | `aa5d08d97773` through `c45d23ac67` | Yes | Broken |
| `fix-hsa-signal-only@{1}` (reflog) | `7d31539b26` (cherry-pick) | Yes | Was broken, reset to `8680505b71` |

## First failing mechanism

The amdgpu driver does not currently set the `vmid` field in MAP_QUEUES
PM4 packets (driver uses `discovery=2`, single-process mode with
`ip_block_mask=0x67`). The original code used `gpuDevice->lastVMID()` as
a fallback when `pkt->vmid` was zero. The VMID threading changes removed
this fallback and replaced it with an unconditional `assert(queue_vmid)`,
which fires when `pkt->vmid == 0`.

The commit message for `aa5d08d97773` acknowledges this:
> "VMID multi-process support will be added in a follow-up."

The assertion is premature — the driver-side VMID setup hasn't been
implemented yet.

## Fix

Either:

1. **Defensive fallback** (match original behavior):
   ```cpp
   const uint16_t queue_vmid = pkt->vmid ? pkt->vmid : gpuDevice->lastVMID();
   ```

2. **Deferred assert** — keep the assert but gate it behind a config
   flag or multi-process mode check, so single-process (current) mode
   doesn't trigger it.

3. **Driver-side fix** — implement VMID setup in the amdgpu driver's
   PM4 packet builder so MAP_QUEUES packets carry a valid VMID.
   This is the long-term correct fix but requires driver changes.

## Secondary effect: QEMU double-error assertion

The gem5 crash masked a QEMU bug in `hw/vfio-user/proxy.c`:
`vfio_user_recv_hdr` passes `errp` to `qio_channel_readv_full`, which
sets `*errp` on failure, then `error_setg_errno` tries to set it again
→ `assert(*errp == NULL)` fails.

This is a defensive bug — the double-set is always wrong, but the path
is only reached when gem5 closes the socket unexpectedly. The fix
(pass `&local_err` and `error_propagate_prepend`) should be applied
regardless.

Upstream QEMU (`gitlab.com/qemu-project/qemu`, master) has the identical
double-error pattern in `proxy.c:234-241`.

## Verification

- `fix-hsa-signal-only` / `fix/ip-discovery-vram` at `8680505b71`:
  GPU init succeeds (`1/1 GPU(s) initialized`)
- Same commit + cherry-picked VMID changes:
  gem5 crash (`assert(queue_vmid)`)
- QEMU proxy.c fix is independently verified but not required for
  single-process mode with working gem5

## Skill routing

- `overview.md` PM4 section
- `../qemu/error-setv-pattern.md` for the QEMU secondary failure chain
- `cosim-gpu-rocm-stack` driver parameter reference (`discovery=2`, `ip_block_mask=0x67`)
