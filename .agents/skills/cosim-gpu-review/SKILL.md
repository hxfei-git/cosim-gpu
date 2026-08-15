---
name: cosim-gpu-review
description: Use when reviewing cosim changes, preparing PR evidence, or delegating independent review. Requires a confirmed review target from cosim-gpu-flow-plan unless the user supplies an explicit standalone review brief.
---

# Cosim Review

Review local cosim changes through an independent reviewer workflow. This skill
owns review orchestration, finding disposition, and PR-ready evidence. Shared
reviewer isolation, prompt hygiene, source-base, build, and formatting rules
live in `references/review/independent-review-contract.md`; read that file
before delegation or maintainer audit.

## Entry Rules

- Start from a `cosim-gpu-flow-plan --type review` plan whose Confirmed Target
  section is complete.
- Exception: if the user explicitly supplies a standalone review brief, inspect
  only the files and artifacts permitted by that brief, do not modify source,
  and return findings ordered by severity.
- Map older names such as `cosim-review` to this skill.
- The user controls round count and stopping rule when they supply them.
  Default: one independent review round, followed by maintainer audit.
- When invoked from `cosim-gpu-rlcr-loop`, follow the RLCR stop rule instead of
  asking after each clean round.

Ask only before widening reviewed scope, changing base reference, changing
target programs or fixed environment rows, using a nonstandard binary,
requesting a full or cold rebuild, editing a disk image, deleting files outside
repository cleanup scripts, accessing external services, or creating a pull
request.

## Workflow

1. Read `references/review/independent-review-contract.md`.
2. Read optional domain references when relevant:
   - `references/review/gem5-format-hygiene.md` for gem5 formatter scope,
     commit splitting, or style churn.
   - `references/review/github-actions-ci.md` for GitHub Actions YAML.
3. Write `artifacts/<slug>/reviews/review-round-N-brief.md` from `plan.md`,
   scoped diffs, test evidence, and public review comments.
4. Send the brief to an independent reviewer through `cosim-gpu-codex-review`.
5. Save output as
   `artifacts/<slug>/reviews/review-round-N-adversarial.md`.
6. Audit every finding and save
   `artifacts/<slug>/reviews/review-round-N-audit.md`.
7. Update `artifacts/<slug>/review-findings.md` with accepted, rejected, fixed,
   or deferred status.
8. After accepted source or behavior changes, run the standard incremental
   build and affected test rows through `cosim-gpu-build` and `cosim-gpu-test`.

If an accepted finding only requests validation coverage, convert it into
explicit test manifest rows. Add only rows already inside the confirmed scope;
return to planning if the requested validation changes program family, binary,
live debug mode, or environment policy.

## Maintainer Audit

The maintainer audit is broader than pass/fail evidence. For each changed area,
judge whether the design can be smaller or clearer: ownership, mutable state,
duplicated completion paths, callback ordering, local invariants, and public
surface. Record accepted design cleanup separately from correctness fixes.

## Stop And Output

Stop when the requested round count or stopping rule is satisfied, all accepted
findings are fixed or deferred with reason, final evidence satisfies build and
provenance gates, and non-PASS rows have debug or invocation-error records.

For PR preparation, write `artifacts/<slug>/pr-body.md` with summary,
motivation, changes, verification commands, and risk notes. Do not create a PR
unless the user explicitly asks.
