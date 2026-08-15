---
name: cosim-gpu-info-gathering
description: Use when collecting, summarizing, auditing, or comparing cosim evidence from artifact directories, large logs, matrices, verdict files, build provenance, or prior conversation records; also use when reducing raw log reads or checking whether existing artifacts lose information. Enforces full raw evidence preservation, bounded reads, source attribution, compact tables, raw-read plans, coverage self-reporting, and unknown-event preservation before analysis.
---

# Cosim Information Gathering

Use this skill before broad log review, prior conversation scans, or cross-artifact
summaries. Its job is to build a compact, attributed evidence map that other
skills can reason from.

## Source Order

Prefer archived artifacts over chat memory and live output:

1. Matrix files: `matrix.final.tsv`, `matrix.tsv`
2. Per-row verdicts: `tests/*/verdict.json`
3. Provenance: `patch/binary-provenance.txt`
4. Logs: `logs/gem5.log`, `logs/qemu.log`, `logs/guest/**/*.log`
5. Runner metadata and guest scripts
6. Prior conversation records, only when the task is explicitly about process
   history or skill behavior

Every extracted fact must retain source file, line number when available, and
the table or rule that produced it.

## Evidence Preservation

Preserve raw evidence before optimizing the conversation. Token savings belong
to what the agent reads and quotes, not to what a diagnostic run records.

For each test row, keep complete archived logs when the runner can provide
them:

- `logs/gem5.log`
- `logs/qemu.log`
- `logs/guest/**/*.log`
- exact command, environment, verdict, matrix row, build hash, and source
  fingerprint

Compact summaries are indexes over raw evidence. They must not be the only copy
of a diagnostic observation unless the observation was never available in raw
form.

## Run Isolation

Keep each evidence intake tied to one explicit source set. Do not mix session
records, live command output, and artifact summaries from different task turns
unless the output names each source and range.

For session extracts, record the exact session path, start line, end line,
whether tool output was included, and the text limit used. For artifact audits,
record the artifact root and generated table directory. A later review should
be able to tell whether two rows came from the same evidence intake or from
different test rounds.

## Bounded Reads

Do not scan home directories, token stores, or unrelated session trees. When a
conversation record is required, identify the exact session file first and parse
only user, assistant, command, and command-output summaries needed for the task.
Use `.agents/skills/cosim-gpu-info-gathering/scripts/codex_session_extract.py`
when available. Do not use `head`, plain `rg`, or broad filesystem search on
JSONL session records.

For session review, keep source file, line range, record kind, and bounded text
visible. Treat developer, token-count, metadata, tool-schema, and raw
tool-output records as out of scope unless the task explicitly targets them.
Tool outputs must be redacted when included.

For logs, start with compact tables:

Generate these tables with `python3 scripts/cosim_artifact_audit.py --root
<artifact-root> --out <artifact-root-or-task>/audit` when the artifact layout is
compatible. If the script cannot cover the layout, record that fact and produce
the same table contract manually.

- `evidence_index.tsv`: available files, roles, and sizes
- `raw_evidence_contract.tsv`: required raw evidence roles and missing files
- `row_status.tsv`: artifact completeness and missing fields
- `verdicts.tsv`: outcome and verdict source
- `provenance.tsv`: gem5 source and binary identity
- `log_availability.tsv`: gem5, QEMU, and guest log presence
- `filter_coverage.tsv`: diagnostic filters, observed extrema, and uncovered counts
- `signals.tsv`: known high-value lines with source and line number
- `candidate_events.tsv`: low-confidence structural log candidates
- `review_queue.tsv`: rows that need agent interpretation
- `raw_read_plan.tsv`: minimal raw files and line windows to inspect

Open raw logs only after these tables identify a row, source file, and line
range.

When a command writes an artifact directory, inspect generated files only after
the command has finished. Do not launch read commands in parallel with the
writer for validation evidence.

## Accuracy Rules

Separate deterministic extraction from interpretation. Scripts may decide file
presence, manifest completeness, verdict fields, exact environment values,
source line numbers, and known signal matches. Agents decide ownership,
mechanism, experiment priority, and source-edit readiness.

Never replace raw evidence with a summary. A summary is valid only when the raw
source path remains available and the extracted row can be regenerated.

When a top-level `evidence.md`, aggregate table, or hand-written summary
conflicts with a per-row artifact, prefer the per-row files from the same
artifact directory: `verdict.json`, `matrix.tsv`, `patch/source-snapshot.txt`,
`patch/binary-provenance.txt`, and logs. Record the summary as stale or
inconsistent. Do not classify the source change as unproven when row-local
source snapshot, binary provenance, and verdict agree.

Filtered diagnostics must self-report coverage. Record the filter expression,
covered object range, final observed object range, and uncovered object count.
If a final waiting object lies outside the filter range, mark the row
`coverage_insufficient` and rerun or reprocess with a wider range before using
the absence of events as evidence.

Rows that did not use diagnostic filters may report `not_applicable` in
`filter_coverage.tsv`. Do not treat missing filter coverage as a defect unless
the manifest, debug flags, or generated artifacts show that filtering was used.

## Unknown Events

Unknown evidence must remain visible. If a non-PASS row has no known signal,
mark it as `nonpass_unclassified`. If a log line looks relevant but does not
match a known rule, record it as a candidate with low confidence rather than
forcing it into an old category.

Candidate extraction should stay structural: use log tails, existing signal
neighborhoods, verdict sources, and exit-code neighborhoods. Do not add broad
keyword sets merely to make more rows look classified.

## Artifact Capacity

Use this when the task asks about `artifacts` size, cleanup, or relocation.

- Start read-only: `du -sh artifacts`, `df -h .`, `df -h <candidate-target>`
  when a relocation target is named, and largest immediate children with
  `du -sh artifacts/* | sort -h`.
- If `artifacts` is a directory symlink, plain `find artifacts` inspects the
  link itself. Use `artifacts/` or an explicit symlink-following mode when
  counting target contents.
- For relocation to external storage, keep phases separate: copy to a new
  target, verify size and entry counts, run an `rsync --dry-run` or equivalent
  no-difference check, rename the original directory, create the project-local
  symlink, verify normal reads through the symlink, then remove the old copy.
- Do not delete evidence solely from directory names. Identify largest files
  and whether they are reproducible worktree/build products, logs, or unique
  verdict/provenance records.
- After cleanup or relocation, report both `artifacts` logical size and root
  filesystem free space. A deleted path may not free space if another large
  artifact appeared or a process still holds removed files.

## Output Contract

A good information-gathering result gives the next skill:

- artifact root and row identity
- outcome, status, and provenance
- missing files or dimensions
- high-value signal rows with source and line number
- filter coverage and any uncovered final objects
- explicit unknown or unclassified rows
- a minimal raw-log read list
- latest user request checked against the final answer topic
