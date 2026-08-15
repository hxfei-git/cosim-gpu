---
name: cosim-gpu-build
description: Use when building gem5 or QEMU, checking build provenance, or preserving gem5 incremental builds across worktrees. Delegates to scripts/cosim_build.sh; never hand-roll Docker, scons, or QEMU make commands.
---

# Cosim Build

Build gem5 and QEMU for cosim through `scripts/cosim_build.sh`. This script is
the single source of truth for compilation, metadata, and incremental skip
behavior.

## Quickstart

```bash
bash scripts/cosim_build.sh gem5
bash scripts/cosim_build.sh qemu
bash scripts/cosim_build.sh all
bash scripts/cosim_build.sh gem5 --force
```

For an isolated gem5 worktree:

```bash
GEM5_DIR=/abs/path/to/gem5-worktree bash scripts/cosim_build.sh gem5
```

When testing that worktree, pass the matching binary explicitly:

```bash
--gem5-bin /abs/path/to/gem5-worktree/build/VEGA_X86/gem5.opt
```

## Required Policy

Read `references/build-policy-and-provenance.md` before deciding whether to
rebuild, accepting binary evidence, using a nonstandard binary, or responding
to stale metadata.

Read `references/worktree-incremental-build.md` before creating an isolated
worktree, copying build outputs, removing an artifact worktree, or accepting
evidence from a nonstandard gem5 worktree.

Do not run raw `docker run ... scons`, direct `scons`, or `make -C qemu/build`
commands as substitutes for the build script.

## Binary Locations

| Component | Binary |
|-----------|--------|
| gem5 | `gem5/build/VEGA_X86/gem5.opt` |
| QEMU | `qemu/build/qemu-system-x86_64` |

The build script creates the Docker image from `scripts/Dockerfile.run` when it
is missing.

## Integration

- `cosim-gpu-flow-plan` records source bases before build evidence matters.
- `cosim-gpu-test` and `cosim-gpu-debug` check provenance before launch.
- After any rebuild, all earlier test evidence is stale for current source
  until rerun with the intended binary.

## Verification

```bash
bash scripts/cosim_build.sh gem5
cat gem5/build/VEGA_X86/.cosim-build-meta
bash scripts/cosim_build.sh gem5
```

The second run should report that gem5 is up to date and skip rebuilding.
