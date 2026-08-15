# Diagnostic And Matrix Runs

Read this for long timeouts, performance rows, interrupt-mode matrices, repeat
runs, or interrupted matrix automation.

## Flow Checkpoint Execution

For an interrupted matrix run, reconstruct state from:

- `artifacts/<task-slug>/tests/run-manifest.tsv`
- top-level `matrix.tsv`
- live `run_cosim_tests.sh`, `cosim_launch.sh`, QEMU, and gem5 processes
- per-row `verdict.json` or `[COSIM_VERDICT]`
- binary provenance and effective environment rows

Treat an interrupted row without verdict as incomplete. If no live process
remains, rerun it with the exact manifest command and output directory. If a
per-row artifact has a verdict but the top-level matrix lacks the row, append
only after the artifact matches the manifest. Otherwise rerun.

For many fixed rows or partial matrix state, also read
`manifest-checkpoint-automation.md`.

## Diagnostic Rows

For timeout confirmation or throughput investigation, launch one fixed
manifest row and wait for the runner artifact. Do not poll live output as final
evidence. Use `cosim-gpu-info-gathering` for archived summaries before reading
large logs.

When debug flags are used, record:

```text
diagnostic_purpose=confirm_hang|throughput_probe|queue_probe|signal_probe
observation_dimension=progress|queue|signal|pressure|failure
debug_flags=<exact gem5 debug flags>
```

Prefer compact generated tables when available:

- `coverage.tsv`
- `filter_coverage.tsv`
- `progress.tsv`
- `queue.tsv`
- `signals.tsv`
- `diagnostic-summary.tsv`

Classify timeout as `slow_progress` when dispatch or completion counters keep
changing. Classify it as a wait candidate only when counters, guest output, and
relevant queue or signal observations stop changing or are missing in a way
that requires a targeted follow-up.

If diagnostic filters do not cover final waiting objects, mark
`coverage_insufficient`, not absence of the event.

## Performance Rows

Keep runner wall time, host CPU time, guest workload window, event counts,
completion counts, verdict, binary hash, source fingerprint, and candidate diff
in one artifact set.

When parsing `/usr/bin/time -v`, preserve raw elapsed text and convert formats
`SS`, `M:SS`, and `H:MM:SS`. Split label and value with the first `": "`
delimiter so elapsed values stay intact.

Report both runner wall-clock change and target workload window when available.
If internal event counts improve but runner wall time does not move, classify
the result as local cleanup or secondary optimization.

## Matrix Runs

For multiple programs, interrupt modes, or repeats, create one top-level
`matrix.tsv` under the task artifact directory and append one row per accepted
run.

For interrupt-mode rows:

```bash
GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=0" bash scripts/run_cosim_tests.sh <program>
GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=1" bash scripts/run_cosim_tests.sh <program>
```

Accept interrupt mode only from `[COSIM_ENV] HSA_ENABLE_INTERRUPT=<value>` in
guest logs or from matrix data generated from that line.

Required matrix columns:

```text
program	hsa_interrupt	run	session_id	outcome	exit_code	reason	artifact_dir
```

For any non-PASS row, report row, artifact directory, and verdict reason, then
invoke `cosim-gpu-debug`. If all rows pass, report pass count, gem5 commit,
gem5 binary hash, and matrix path.
