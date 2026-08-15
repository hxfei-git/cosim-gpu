---
name: cosim-gpu-test
description: Use when running GPU programs against cosim and classifying outcomes. Requires exact program identity, fresh-session runner use, verdict artifacts, and provenance checks. After cosim-gpu-flow-plan for planned test tasks.
---

# Cosim Test

Execute GPU programs against cosim and classify results. The test runner owns
launching, guest execution, artifact routing, source snapshot, provenance,
matrix rows, verdicts, and cleanup.

Use `cosim-gpu-launch` only for manual boot or interactive inspection. Use this
skill whenever a program result, matrix row, verification result, or PR-grade
test artifact is required.

## Quickstart

```bash
bash scripts/run_cosim_tests.sh vector_add
bash scripts/run_cosim_tests.sh --all
bash scripts/run_cosim_tests.sh HIP-Basic/FloydWarshall
bash scripts/run_cosim_tests.sh --test-timeout 120 --output-dir artifacts/<slug> vector_add
bash scripts/run_cosim_tests.sh --hang-env --output-dir artifacts/<slug> bit_extract_sync
```

`scripts/run_cosim_tests.sh` has two modes:

- `pure_test`: fresh cosim session, guest workload, classification, artifact
  archive, cleanup.
- `hang_env`: launch cosim, mount the shared repository, build the target,
  write `category=hang_env_ready`, and leave the console pipe alive for
  `cosim-gpu-guest`.

Normal tests always archive evidence and clean the live session.

## Required Gates

Before launching a named program, prove exact program identity. Read
`references/program-identity-and-guest-env.md` for local kernels, ROCm
examples, variants, timeout estimation, device-side `printf`, or
`GUEST_TEST_PREFIX`.

Before automated rows, repeats, environment matrices, non-default binaries, or
evidence intended to prove uncommitted source, read
`references/run-manifest-and-provenance.md`.

For long diagnostic rows, performance rows, matrix runs, or interrupted test
automation, read `references/diagnostic-and-matrix-runs.md`.

Use `cosim-gpu-build` for build readiness and binary provenance decisions. Do
not duplicate build metadata rules here.

## Automation Rule

When program identity, manifest rows, and required provenance checks are
complete, run `scripts/run_cosim_tests.sh` directly. Do not ask before standard
test execution, repeats, interrupt-mode matrix rows, timeout probes, post-fix
reruns inside the current scope, standard incremental build, or standard
preflight cleanup.

Ask only before changing target program, changing fixed environment values,
using a nonstandard binary, full or cold rebuilds, deletion outside repository
cleanup scripts, or switching from normal test mode to live debug mode when the
target is not already known to hang.

## Result Classification

Tests are classified by `scripts/classify_runs.py --json`. The agent must read
`[COSIM_VERDICT]` or `verdict.json` from the test artifact and report the
outcome verbatim. Do not override verdict artifacts by manually interpreting
logs.

| Outcome | Meaning | Action |
|---------|---------|--------|
| `PASS` | Program ran, output validated, exit 0 | Record and proceed |
| `FAIL` | Anything not PASS | Route by verdict reason |

Use verdict `reason` for routing. Examples: timeout, wrong result, simulator
death, boot timeout, or invocation error. If QEMU or gem5 exits before the
guest test starts, treat it as launcher/backend evidence, not a program result.

For existing logs:

```bash
python3 scripts/classify_runs.py --log-dir artifacts/<slug>/logs [--matrix artifacts/<slug>/matrix.tsv]
```

## Integration

- Before planned test work: `cosim-gpu-flow-plan --type test`
- Before accepting evidence: build/provenance gate from `cosim-gpu-build`
- Before launch: `bash scripts/cosim_cleanup.sh`
- After any non-PASS row: `cosim-gpu-debug`
- Output authority: per-row artifacts under `artifacts/<slug>/`, matrix rows,
  guest logs, gem5 log, QEMU log, verdict, source snapshot, and binary
  provenance

Final matrices must prove program identity, outcome, guest log, artifact path,
gem5 commit, gem5 binary path, and gem5 binary hash for every accepted row.
Manifest values are pre-run contracts, not proof of execution.
