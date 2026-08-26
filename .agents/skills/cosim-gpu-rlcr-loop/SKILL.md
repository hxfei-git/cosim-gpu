---
name: cosim-gpu-rlcr-loop
description: Use for iterative cosim implementation, debugging, or validation after cosim-gpu-flow-plan. Runs bounded rounds until frozen acceptance criteria and independent review gates pass.
---
# Cosim RLCR Loop

Run iterative implementation or debugging after `cosim-gpu-flow-plan`. This
skill owns round state, acceptance criteria, interruption recovery, and the
order of build, test, and review gates. Independent reviewer rules live in
`../cosim-gpu-review/references/review/independent-review-contract.md`; read
that file before each review gate.

## Required Inputs

- `artifacts/<task-slug>/plan.md`
- frozen acceptance criteria
- chosen domain skill for the technical work
- build and provenance gate from `cosim-gpu-build`
- current `HEAD` recorded as the source-edit base

If there is no plan, run `cosim-gpu-flow-plan` first.

## Artifact Layout

```text
artifacts/<task-slug>/rlcr/
├── goal-tracker.md
├── round-001-summary.md
├── round-001-review.md
└── final-summary.md
```

Logs, review briefs, verdicts, and provenance stay in the task workspace under
`logs/`, `reviews/`, `tests/`, or existing artifact paths.

## Goal Tracker

Maintain `rlcr/goal-tracker.md`:

```markdown
# Goal Tracker

## Immutable
- Goal:
- Acceptance Criteria:

## Mutable
- Active round:
- Completed:
- Remaining:
- Deferred with reason:
- Decision log:
```

Do not change the immutable section without explicit human approval.

## Round Loop

Repeat until all acceptance criteria pass and the independent review stop rule
is satisfied.

Stop rules apply in this order:

- user-supplied stop rule in `plan.md` or `goal-tracker.md`
- frozen acceptance criteria plus required evidence gates
- default convergence: two consecutive independent reviews with no new
  `BLOCKER` or `MAJOR` findings

Each round:

1. Select the smallest coherent objective that advances a named acceptance
   criterion.
2. Re-read current evidence, acceptance criteria, source base, and pending
   findings.
3. For bug/debug tasks, write at least two mutually exclusive hypotheses to
   `scratch/hypotheses.md` before source edits.
4. Implement or investigate using the selected domain skill.
5. After any source change, treat prior test rows as non-final.
6. Run the standard incremental build and relevant test rows.
7. Write `rlcr/round-NNN-summary.md` with commands, artifacts, verdicts,
   source base, and remaining work.
8. Run the independent review gate through `cosim-gpu-review` or
   `cosim-gpu-codex-review`, following the shared review contract.
9. Audit findings, fix accepted in-scope issues, and defer wider redesigns with
   reasons.

Ask only when the next action would alter the immutable goal, widen source or
test scope, use a nonstandard binary, perform a full or cold rebuild, edit a
disk image, access external services, or create public artifacts.

## Interruption Recovery

After interruption or context compaction, reconstruct state from artifacts:

- `plan.md`
- `rlcr/goal-tracker.md`
- latest round summary and review files
- `evidence.md`, `commands.md`, matrix rows, verdicts, and provenance
- live build or test processes

If no live process remains and the artifact lacks a valid verdict or review
result, classify the interrupted step as incomplete and resume the next missing
step inside the frozen scope. Do not ask whether to continue when the remaining
step is already authorized by the plan.

If `HEAD` moved, record the new value and reclassify pending findings against
current source before editing.

## Verification Gate

Use the narrowest relevant gate first:

- build readiness through `cosim-gpu-build`
- provenance artifacts from the standard test path
- smoke row for the changed path
- exact target rows named by the plan, user, or reviewer
- variants such as interrupt mode or repeated runs only when required
- independent review through the shared review contract

The final row must have `verdict.json` or a `[COSIM_VERDICT]` line. Do not
override verdict artifacts by interpreting logs manually.

## Final Output

Write `rlcr/final-summary.md` with:

- acceptance criteria status
- final source base and touched repositories
- build and test commands
- verdict, matrix, and provenance paths
- accepted, fixed, rejected, and deferred review findings
- remaining risks outside the frozen scope
