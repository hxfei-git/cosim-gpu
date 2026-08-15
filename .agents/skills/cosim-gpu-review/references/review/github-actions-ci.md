# GitHub Actions CI Review

Use this reference when reviewing `.github/workflows/*.yml` or `.yaml`
changes, especially path-classified fast paths and aggregate required checks.

## Required Checks

1. List every job referenced through `needs.<job>.result` or
   `needs.<job>.outputs.*`, then verify that each job is present in the
   aggregate job's `needs:` list.
2. Check both lanes of any classifier output. For each lane, name which jobs
   must be `success`, which jobs may be `skipped`, and which skipped job would
   indicate a missing gate.
3. Treat workflow-file changes as full-CI unless the task explicitly proves a
   safe narrower policy. CI logic should not validate itself only through the
   fast path it edits.
4. For shell `case` classifiers, verify actual pattern semantics with a small
   shell command when the result matters. In POSIX shell patterns, `*` can match
   `/`, so `src/dev/amdgpu/*` may cover deeper paths.
5. Separate allow-list and deny-list reasoning. Files that can affect vfio-user,
   architecture build scripts, unit tests, or shared headers used by full-CI
   targets should either force full CI or have a recorded reason why the fast
   path is sufficient.
6. Verify that adding a fast-path job does not add work to general PRs. A
   replacement job should run only on the lane where it replaces skipped broad
   checks.

## Evidence To Record

- Workflow file path, base commit, and head commit or working tree path.
- The classifier variable names and all possible values.
- A table of lane to required jobs.
- Any shell pattern probes used to confirm classifier behavior.
- Any path class intentionally forced to full CI, such as workflow files,
  vfio-user sources, architecture build scripts, or test infrastructure.

## Finding Rules

Classify as high severity when a relevant path can reach the aggregate required
check without a required compile, unit, quick-test, vfio-user, macOS, clang, or
GPU gate. Classify as medium when the workflow is safe but does extra work for
general PRs or has brittle classifier coverage that should be narrowed before
merge.
