# Policy And Checkpoint

Read this when the task spans multiple turns, has an active user goal, or needs
automation after a confirmed target.

## Automation Boundary

The planning gate is where human control belongs. If the plan has a complete
and confirmed target, later fixed execution steps may run without repeated
approval when they are read-only or use repository-owned scripts with an
accepted manifest.

Explicit broad approval for the current target covers standard builds, fixed
manifest rows, same-target reruns, local log reads, and diagnostic-only
instrumentation inside the accepted scope. It does not cover target changes,
new binaries, cold rebuilds, disk-image edits, destructive cleanup, external
services, or public artifacts.

Allowed automated steps after target confirmation:

- standard incremental builds required by `cosim-gpu-build`
- standard preflight cleanup required by launch or test skills
- fixed test rows from a complete run manifest
- fixed environment matrix rows named by the plan
- post-fix reruns inside the same scope
- local log, diff, and artifact reads
- diagnostic-only source instrumentation guarded by debug flags when the active
  debug plan names the file scope
- checkpoint reconstruction from an active artifact workspace
- rerun of an incomplete manifest row when no matching live process remains
- summary updates that copy already-proven verdict, matrix, and provenance
  facts into active evidence files

Ask before actions that change target, widen review scope, switch to a
nonstandard binary, request a full or cold rebuild, edit a disk image, delete
files outside repository cleanup scripts, access external services, or create
public artifacts.

## Goal Scope

- Keep broad objective and phase objective distinct.
- Before marking a goal complete, name acceptance criteria and artifact paths.
- If work changed from exploration to a diagnostic stage, record whether that
  stage replaces the active goal or only advances it.
- Do not mark a broad optimization goal complete merely because a bottleneck
  class was found.
- When asked about goal status, answer with phase status and remaining parent
  objective, if any.

## Checkpoint Execution

Use this after interruption, context compaction, or a user message such as
`continue`.

Reconstruct state from the active artifact workspace before asking whether to
proceed:

- `plan.md`
- `commands.md`
- `matrix.tsv`
- run manifests
- build provenance
- live process state

Continue only steps authorized by the task boundary:

- Review tasks: Confirmed Target and required evidence gates.
- Test tasks: accepted run manifests and fixed environment rows.
- RLCR tasks: frozen acceptance criteria and `rlcr/goal-tracker.md`.
- Bug/debug tasks: plan scope and evidence ledger.

When executing from a checkpoint:

- If a command lacks a verdict or review artifact, classify it as incomplete.
- If no live process remains and the row is still accepted, rerun through the
  standard skill path.
- If a live process remains, observe or clean it only through the relevant
  repository-owned skill.
- If a long-running test is alive, wait for runner artifacts or collect a
  bounded live-state sample only when the debug plan asks for it.
- If runner artifacts have verdict, matrix, and binary provenance, treat them
  as authoritative over chat observations.
- Ask only if the remaining step changes target, scope, binary, environment
  values, workload set, or rebuild policy.

## Artifact Relocation

For large artifact relocation to external storage, keep copy, verification,
link replacement, and old-copy deletion as separate steps. Never remove the
original directory until copied target counts or size match, a dry-run sync
reports no remaining changes, and ordinary repository paths work through the
new symlink.
