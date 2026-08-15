# Manifest Checkpoint Automation

Use this reference when a cosim test or debug matrix is driven by
`tests/run-manifest.tsv` and work was interrupted, compacted, or split across
multiple Codex turns.

## Authority Order

1. Exact row in `tests/run-manifest.tsv`.
2. Per-row `metadata.txt`, `verdict.json`, `matrix.tsv`, and
   `patch/binary-provenance.txt`.
3. Top-level `matrix.tsv` or task evidence file.
4. Chat observations, only after they have been converted into artifact facts.

Do not treat chat text, partial terminal output, or live container output as a
final result when archived runner files exist.

## Row State

Classify each manifest row before acting:

- `complete`: artifact directory is unique, `verdict.json` or
  `[COSIM_VERDICT]` exists, matrix row exists, effective environment matches the
  manifest, and binary provenance contains the required gem5 fields.
- `incomplete-live`: the row lacks a verdict and a matching runner, QEMU, or
  gem5 process is alive.
- `incomplete-dead`: the row lacks a verdict and no matching live process
  remains.
- `invalid`: program identity, environment, binary path, output directory, or
  provenance does not match the manifest.

## Actions

- For `complete`, summarize the archived verdict and provenance. Do not rerun.
- For `incomplete-live`, wait for the runner artifact unless the debug plan
  explicitly asks for bounded live-state sampling.
- For `incomplete-dead`, rerun the exact manifest row with the same output
  directory, binary, timeout, environment, and runner argument.
- For `invalid`, record a preflight or provenance failure and do not substitute
  a nearby program, timeout, binary, or environment.

## Long Timeout Rows

For rows intended to test larger time budgets, verify the guest-side archived
script or metadata shows the requested timeout. If the runner command used a
longer host timeout but the guest script still contains the old timeout, mark
the row as an invocation defect rather than model evidence.

When a row times out with valid long-timeout evidence, classify from archived
logs only. Prefer compact extracted facts: last completion count, last dispatch
count, active wave state, fatal or panic presence, and binary provenance.

## Evidence Record

Record the following in the task evidence:

- manifest row identifier and exact output directory
- live process decision, if the row was interrupted
- final verdict source file
- effective guest environment
- gem5 source commit, commit subject, binary path, and binary hash
- whether the timeout value was proven inside the guest-side artifact
