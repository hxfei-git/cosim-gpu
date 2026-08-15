# Worktree Incremental Build Reuse

Use this reference when a task needs isolated gem5 source edits while preserving
incremental build behavior. Typical triggers are concurrent Codex conversations,
parallel debug experiments, or a user request to explicitly specify a gem5
worktree.

## Contract

- Keep `scripts/cosim_build.sh` as the only build interface.
- Use `GEM5_DIR` to select the intended gem5 worktree.
- Use `--gem5-bin` to select the matching `gem5.opt` for tests or launchers.
- Treat copied build output as a warm starting point, not proof that no rebuild
  is needed.
- Do not classify rows from a worktree as valid evidence unless provenance was
  recorded from that same worktree.

## Procedure

1. Record the source worktree state:

```bash
git -C gem5 rev-parse HEAD
git -C gem5 log -1 --format='%H%n%s%n%b'
cat gem5/build/VEGA_X86/.cosim-build-meta
stat -c '%n %s %y' gem5/build/VEGA_X86/gem5.opt
sha256sum gem5/build/VEGA_X86/gem5.opt
```

2. Create an isolated worktree under the active artifact workspace. Prefer a
   detached worktree when the user does not request a branch:

```bash
git -C gem5 worktree add --detach /abs/path/to/artifacts/<task>/worktrees/gem5-debug <commit>
```

3. Copy the existing build directory into the new worktree. Prefer reflinks
   because the build directory may be large:

```bash
cp -a --reflink=auto gem5/build /abs/path/to/artifacts/<task>/worktrees/gem5-debug/build
```

4. Refresh the copied build output through the standard script:

```bash
GEM5_DIR=/abs/path/to/artifacts/<task>/worktrees/gem5-debug bash scripts/cosim_build.sh gem5
```

5. Verify the new worktree has its own build metadata:

```bash
git -C /abs/path/to/artifacts/<task>/worktrees/gem5-debug rev-parse HEAD
git -C /abs/path/to/artifacts/<task>/worktrees/gem5-debug log -1 --format='%H%n%s%n%b'
cat /abs/path/to/artifacts/<task>/worktrees/gem5-debug/build/VEGA_X86/.cosim-build-meta
stat -c '%n %s %y' /abs/path/to/artifacts/<task>/worktrees/gem5-debug/build/VEGA_X86/gem5.opt
sha256sum /abs/path/to/artifacts/<task>/worktrees/gem5-debug/build/VEGA_X86/gem5.opt
```

6. Run a second build check when time permits. A skip message proves the copied
   directory has become an independent incremental baseline for that worktree:

```bash
GEM5_DIR=/abs/path/to/artifacts/<task>/worktrees/gem5-debug bash scripts/cosim_build.sh gem5
```

Expected skip text:

```text
gem5 up to date (commit <sha>), skipping build
```

7. Launch tests with the matching binary path:

```bash
--gem5-bin /abs/path/to/artifacts/<task>/worktrees/gem5-debug/build/VEGA_X86/gem5.opt
```

## Interpretation

The copied build directory may still rebuild generated files, object files, or
`gem5.opt` on the first pass. This is expected because the build system can
store absolute paths, command signatures, dependency scans, and source
fingerprints. The important distinction is whether the first pass is smaller
than a cold build and whether the second pass skips or performs only local
incremental work.

If the standard `gem5` worktree changes while the isolated worktree remains at
its intended commit, treat that as evidence that the isolation is working. It
does not by itself prove any test result; test evidence still depends on the
explicit `--gem5-bin` path and matching metadata.

## Evidence to record

- Worktree path and creation command.
- Source commit hash and commit message used for the worktree.
- Source worktree commit hash and commit message when copied build output came
  from another path.
- Whether `gem5/build` was copied with `--reflink=auto`.
- First build log path and elapsed time.
- Second build log path and skip or rebuild outcome.
- Worktree `build/VEGA_X86/.cosim-build-meta`.
- `stat` and `sha256sum` for the worktree `gem5.opt`.
- Exact `--gem5-bin` value used by every accepted test row.
