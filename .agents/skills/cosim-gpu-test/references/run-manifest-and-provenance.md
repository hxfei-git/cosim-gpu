# Run Manifest And Provenance

Read this when a cosim test task uses automation, repeats, environment rows,
non-default binaries, or evidence that must prove an uncommitted source state.

## Manifest Gate

Before automation, write or append
`artifacts/<task-slug>/tests/run-manifest.tsv`. Each row must name:

- exact program identity
- mode: `pure_test` or `hang_env`
- repeat count
- timeout policy
- literal `GUEST_TEST_PREFIX`, if any
- expected `HSA_ENABLE_INTERRUPT`
- gem5 binary path
- gem5 config arguments, if any
- output directory
- exact `artifact_dir`, or an `artifact_dir_pattern` that will resolve to one
  row after launch
- matrix path
- provenance file
- literal runner argument
- guest bridge path policy, when the runner uses a host-created script or
  temporary output directory that must be visible inside the guest

Automation may execute only complete rows. Do not infer the target from nearby
filenames, prior conversation, partial filters, or failed discovery commands.
`--all` is valid only when the user explicitly requests all tests or the active
plan names it as the scope.

If a row has ambiguous program identity, conflicting timeout policy, unknown
guest environment, missing artifact path, missing matrix path, missing
binary/provenance data, or a mode mismatch, record a preflight failure and do
not launch it.

Artifact paths and guest bridge paths are different concerns. The artifact
directory is the durable evidence destination and may follow local host storage
policy. The guest bridge path is a transient path used by scripts executing
under `/mnt`; it must resolve inside the mounted share without relying on host
absolute paths or symlinks that leave the share. Prefer a configurable
repository-relative bridge path, archive the bridge script and guest output into
the artifact directory, and remove transient bridge contents during cleanup.

## Artifact Resolution

Use `artifact_dir` when known before launch. Otherwise resolve the exact
directory immediately after launch from:

1. runner output
2. `metadata.txt` run id
3. a unique directory matching `artifact_dir_pattern`

Zero or multiple matches is a checkpoint failure. Do not accept the row until a
single exact artifact directory is recorded.

## Acceptance Checks

Final acceptance must come from artifacts, not from the manifest alone. Verify:

- `metadata.txt` and `matrix.tsv` under `artifact_dir`
- `verdict.json` or `[COSIM_VERDICT]` from the same `artifact_dir`
- effective guest environment from `[COSIM_ENV] HSA_ENABLE_INTERRUPT=<value>`
- provenance file contains `gem5_source_commit`, `gem5_binary`, and
  `gem5_sha256`
- effective gem5 config arguments match the manifest when
  `--gem5-config-arg` is used
- `patch/source-snapshot.txt` exists, has no `error=not_a_git_repository`, and
  records `head_commit` plus `source_fingerprint`
- `patch/gem5-status.txt` exists
- `patch/gem5.patch` represents `git diff --binary --no-ext-diff HEAD`; if it
  is empty, tracked modifications must also be absent
- untracked source files listed in `patch/untracked-files.txt` have
  `patch/untracked-files.tar`
- the top-level matrix contains the row copied or summarized from the same
  artifact directory

If source snapshot fails, classify the row as evidence-incomplete. Binary
provenance can identify the executable, but cannot replay uncommitted source
changes by itself.

## Build Gate

For review, RLCR, debug validation, and PR evidence, use the build and
provenance gate from `cosim-gpu-build`. Pass the standard gem5 binary
explicitly when needed:

```bash
bash scripts/run_cosim_tests.sh \
  --gem5-bin gem5/build/VEGA_X86/gem5.opt \
  --test-timeout 120 \
  --output-dir artifacts/<slug>/<case> \
  <program>
```

The runner writes provenance artifacts before launch. If that gate fails,
follow `cosim-gpu-build`; do not duplicate its metadata rules here.
