# Debug Analysis Reference

Use this reference after `cosim-gpu-debug` has collected the first log set. The goal
is to keep analysis concrete: name the observed behavior, compare it with a
nearby passing run when possible, map the difference to a model mechanism, and
only then edit source.

## Record the observed behavior

Write these fields in the case notes:

- target program and exact relative path
- launch command and artifact directory
- verdict, exit code, timeout status, or crash text
- interrupt mode and other test variables
- guest-visible object when known: signal address, queue, packet, buffer,
  VMID, PASID, interrupt cookie, or wait channel
- log paths and line ranges that prove the behavior

If the guest-visible object is unknown, add read-only markers or collect guest
state again before editing source.

## Compare with a passing run

Prefer the nearest passing run in this order:

1. same program with another interrupt mode
2. same program on an earlier gem5 commit
3. different program that exercises the same API and completion path
4. minimal baseline such as `vector_add` only when it uses the relevant path

The comparison must use the same launcher, binaries, backend, and device setup
unless that variable is the point of the test.

## Count differences

Count named differences instead of relying on broad impressions:

- signal and interrupt writes
- PM4 packet sequence
- DMA records
- VMID and PASID values
- address translation paths
- cache maintenance events
- wait channels and user-space backtraces for hangs

Use `observable-dimensions.md` as the checklist for visible boundaries. Do not
turn the checklist into a staged process.

## Map difference to mechanism

Every proposed source edit needs a mechanism with a concrete file and function.
Useful references:

- `../gem5-model/overview.md`
- `../gem5-model/cache-coherence-checkpoints.md`
- `../gem5-model/vmid-pasid-architecture.md`
- `../gem5-model/hsa-signal-completion-pattern.md`
- `../gem5-model/discovery-log.md`

For QEMU exits that follow a gem5 crash, use `../qemu/error-setv-pattern.md`
only to explain the secondary symptom.

## Edit and verify

Before editing source, record:

- failing evidence before the change
- source location and mechanism
- intended behavior after the change
- verification rows, including the original failure and at least one nearby
  passing baseline

After editing, run the same test path through the standard runner so binary
provenance and source checks are regenerated.
