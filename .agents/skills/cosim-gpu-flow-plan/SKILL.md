---
name: cosim-gpu-flow-plan
description: Use as the first step for non-trivial cosim testing, debugging, review, or iterative implementation tasks. Do not use for ordinary repository maintenance such as commit splitting, submodule pointer updates, author config, or documentation-only cleanup.
---

# Cosim Flow Plan

Use as the first step for non-trivial cosim tasks: testing, bug localization,
code review, or iterative implementation. It creates a standardized task
workspace and hands off to the domain skill.

Do not use this skill for ordinary repository maintenance: commit splitting,
submodule pointer updates, history rewriting requested only for commit hygiene,
author configuration, `.gitignore` changes, documentation-only cleanup, or
skill-directory refactoring that does not require cosim execution evidence.

## Hard policy

- For cosim test, debug, review, and RLCR tasks, do not modify source files
  before the plan is written and the evidence ledger has confirmed the target.
- Review tasks require explicit human confirmation of the target before any
  reviewer delegation, test execution, or source edit. The confirmed target must
  name the reviewed change, base reference, file or subsystem scope, allowed
  source changes, required evidence, and stopping rule if one is supplied.
- All agent-created artifacts stay under `artifacts/<task-slug>/`.
- Do not add AI roles such as Codex or Claude to contribution trailers. When a commit needs `Signed-off-by`, use the human author identity from `git config user.name` and `git config user.email`.
- Formatting must be diff-scoped. Whole-file or project-wide formatter output
  mixed with behavior changes is a hard failure unless the confirmed task is
  explicitly formatting-only. Prefer `clang-format-diff`, gem5's modified-range
  style tools, or an equivalent patch-range formatter; otherwise format only
  the changed hunk and its immediate context. Before commit, compare normal
  diff stats with whitespace-ignored diff stats and remove unrelated formatter
  churn.
- Optimization edits must be based on current `HEAD`. A historical commit may
  be the reviewed change, but source changes are applied to checked-out `HEAD`
  after recording `git rev-parse HEAD` and whether the reviewed commit is an
  ancestor of `HEAD`. If later commits already changed the same area, classify
  findings against current `HEAD` before editing.

## Workspace

```text
artifacts/<task-slug>/
├── plan.md
├── evidence.md
├── commands.md
├── patch/
│   ├── base-commits.txt       # repo path, branch, hash, and commit message
│   └── binary-provenance.txt
├── logs/
├── state/
├── tests/                 # per-run test artifacts
├── reviews/               # independent review briefs and outputs
├── matrix.tsv          # test tasks only
├── review-findings.md   # review tasks only
├── rlcr/                 # iterative implementation/debug/validation tasks only
└── scratch/
```

## Task types

Choose one template based on the task.

For authorization boundaries, goal status, artifact relocation, or checkpoint
continuation, read `references/policy-and-checkpoint.md`.

### Test (`--type test`)

Goal: execute programs against cosim and classify outcomes.

Template fields:
- program list, source repo/path, build target
- interrupt mode (`HSA_ENABLE_INTERRUPT=0` or `1`)
- execution mode (`single`, `repeated`, `multi`)
- timeout strategy (two-phase: probe → precise)
- expected baseline (reference working case)

Output: `matrix.tsv` with per-program outcome rows.

Hand off to: `cosim-gpu-test`.

### Debug (`--type bug`)

Goal: prepare a workspace for log-driven debug investigation.

Template fields:
- initial symptom and outcome class
- failing program or workload
- expected nearest working baseline, if known
- evidence paths already available
- allowed investigation scope

Hand off to: `cosim-gpu-debug`.

### Review (`--type review`)

Goal: review local changes before pushing through the independent reviewer
workflow.

Template fields:
- commit or PR reference
- current `HEAD` used for any optimization edits
- whether the reviewed commit is an ancestor of `HEAD`
- review scope (subsystem, files)
- explicit user-confirmed review target
- requested round count, if specified
- stopping rule, if specified
- required evidence gates
- allowed automated tests and fixed environment rows

Do not classify review tasks into quick/full modes. `cosim-gpu-review` owns the
independent review workflow; the user controls only the round count and stop
condition when they specify them.

Hand off to: `cosim-gpu-review`.

### RLCR (`--type rlcr`)

Goal: run iterative implementation, debugging, or validation rounds.

Template fields:
- round objective and acceptance criteria
- owning domain or operational skill
- verification gates
- independent review path
- stop condition

Hand off to: `cosim-gpu-rlcr-loop`.

## Plan Template

Read `references/plan-template.md` when creating `plan.md`.

## Composition

After `cosim-gpu-flow-plan`, invoke the domain skill:
- Test task → `cosim-gpu-test`
- Bug task → `cosim-gpu-debug`
- Review task → `cosim-gpu-review`
- RLCR task → `cosim-gpu-rlcr-loop`

Domain skills read `plan.md` from the active workspace. Use `cosim-gpu-build` for
all build and provenance decisions.
