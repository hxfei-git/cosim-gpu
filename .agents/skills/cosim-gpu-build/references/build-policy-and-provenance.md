# Build Policy And Provenance

Read this when deciding whether to build, accepting binary evidence, using a
nonstandard binary, or evaluating stale provenance.

## Autonomy Rule

Run the standard metadata inspection and incremental build path directly.
`bash scripts/cosim_build.sh gem5`, `qemu`, or `all` is the authorized build
interface whenever a task needs a current binary or provenance reports stale
metadata.

Missing metadata, stale metadata, missing binary, or source edits since the
last accepted test are execution conditions, not user decision points.

Do not pause before standard incremental builds, build metadata reads, binary
hash reads, or local Docker image creation performed by
`scripts/cosim_build.sh`. If the execution platform requires authorization,
request only the narrow build-script prefix.

Ask only before a full or cold rebuild, deleting build directories, creating a
fresh worktree only for rebuild, changing the standard binary path, or using
`--force` when stale metadata does not require it.

## Default Decision Rule

Inspect existing binary and metadata before rebuilding:

```bash
git -C gem5 rev-parse HEAD
git -C gem5 log -1 --format='%H%n%s%n%b'
cat gem5/build/VEGA_X86/.cosim-build-meta
sha256sum gem5/build/VEGA_X86/gem5.opt
```

Run `bash scripts/cosim_build.sh gem5` when the binary is missing, metadata is
missing, recorded commit or source fingerprint differs from current source, or
a source edit invalidated prior rows. This remains the standard incremental
path.

Use `--force` only for an explicit user request to prove the build pipeline or
for explicit end-to-end build confirmation.

Full clean rebuilds, such as deleting `build/VEGA_X86`, are almost never
appropriate during debugging. A suspected cache issue is not enough by itself;
record the suspicion and use provenance checks plus incremental rebuilds unless
the user explicitly requested a full rebuild.

Do not create a new worktree or cold-build gem5 just to prove which binary a
test used. Pass the intended binary path explicitly and record metadata plus
hash in the active artifact workspace.

## Rebuild Metadata

For gem5, `scripts/cosim_build.sh` writes
`gem5/build/VEGA_X86/.cosim-build-meta`:

```text
commit=<sha>
source_fingerprint=<sha256>
timestamp=<iso8601>
target=VEGA_X86
```

If current commit and source fingerprint match metadata, the build is skipped.
If rebuild is needed, rely on the script's incremental behavior.

For QEMU, metadata lives in
`.local/cosim/build/qemu-10.1.5/.cosim-build-meta`. Rebuild
incrementally only when binary or metadata is missing, metadata mismatches,
source changed, or QEMU build behavior itself is under test.

## Source And Binary Provenance

For PR, review, RLCR, or bug-localization tasks, treat
`gem5/build/VEGA_X86/gem5.opt` as the only valid gem5 test binary unless the
plan explicitly names another binary.

Before accepting a test row as evidence, record:

```bash
git -C gem5 rev-parse HEAD
git -C gem5 log -1 --format='%H%n%s%n%b'
cat gem5/build/VEGA_X86/.cosim-build-meta
stat -c '%n %s %y' gem5/build/VEGA_X86/gem5.opt
sha256sum gem5/build/VEGA_X86/gem5.opt
```

If a launch log shows `<repo-root>/build/VEGA_X86/gem5.opt` or
container path `/gem5-bin/gem5.opt`, classify that row as an alternate-binary
sample. It must not prove a current gem5 PR unless the plan targeted that
binary.

For a planned isolated worktree, the accepted binary is the explicitly named
`<worktree>/build/VEGA_X86/gem5.opt`. Record `GEM5_DIR`, worktree `HEAD`, build
commit message, build metadata, binary stat, and binary hash from that same
worktree before accepting test evidence.
