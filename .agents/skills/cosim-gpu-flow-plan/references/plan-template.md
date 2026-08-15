# Plan Template

Read this when creating `artifacts/<task-slug>/plan.md`.

```markdown
# <Task Title>

## Goal

## Policy
- All artifacts under artifacts/<task-slug>/
- No source edits before evidence confirms target.
- Formatting must be diff-scoped. Whole-file formatter churn mixed with
  behavior changes fails acceptance unless this is a formatting-only task.
- Review tasks require explicit human confirmation of reviewed change, base
  reference, scope, allowed source changes, evidence gates, and stopping rule
  before reviewer delegation, test execution, or source edits.
- Optimization edits target current `HEAD`; historical commits are review
  inputs unless the user explicitly requests history rewriting.
- Commit provenance must include hash and commit message for every repository
  whose state affects evidence.

## Task type
<!-- test | bug | review | rlcr -->

## Scope
### In scope
### Out of scope
### Allowed source changes

## Confirmed Target
<!-- required for review tasks before execution -->
- Reviewed change:
- Base reference:
- Optimization base:
- Reviewed change ancestry:
- File or subsystem scope:
- Allowed automated tests:
- Fixed environment rows:
- Stopping rule:

## Build and provenance gate
- Use the gate defined by `cosim-gpu-build`.
- Record base commits in `patch/base-commits.txt` with repository path, branch
  or detached state, `HEAD` hash, and commit subject.
- Record formatter-scope evidence when style churn is part of the risk.

## Acceptance Criteria
- AC-1:
  - Evidence:

## Evidence Ledger
| Step | Action | Artifact | Result |

## Open Questions

## Discarded Hypotheses
| Hypothesis | Why discarded | Evidence |
|---|---|---|

## Decision Log
```

## Creation Steps

1. Create workspace:
   `mkdir -p artifacts/<task-slug>/{patch,logs,state,scratch,rlcr,reviews,tests}`.
2. Write `plan.md` using the task type.
3. Use `cosim-gpu-build` before accepting build or test evidence.
4. Write `patch/base-commits.txt` for top-level cosim plus relevant nested
   repositories such as gem5 and QEMU.
5. State whether final evidence must come from the intended test path or older
   rows are comparison samples.
6. For review or RLCR optimization of a historical commit, record current
   `HEAD`, ancestry relation, and that edits target current `HEAD`.
7. Add a checkpoint note for multi-turn tasks.
8. Hand off to the next domain skill.
