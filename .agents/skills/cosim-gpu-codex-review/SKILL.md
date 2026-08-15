---
name: cosim-gpu-codex-review
description: Use when delegating independent code review, design review, or focused bug investigation to an AI reviewer. Provides delegation templates and records reviewer evidence; higher-level review flow remains in cosim-gpu-review or cosim-gpu-rlcr-loop.
---

# Codex Review

Use an independent AI reviewer for bounded technical review, investigation, or
design critique. This skill owns delegation mechanics only. Review policy shared
with `cosim-gpu-review` and `cosim-gpu-rlcr-loop` lives in
`../cosim-gpu-review/references/review/independent-review-contract.md`; read it
before adversarial review delegation.

## Scope

Use this skill for:

- adversarial code review from a written brief
- single-file or subsystem investigation with explicit questions
- design critique of a proposed local change
- second opinion when the main investigation is stuck

Do not delegate user interaction, task scoping, build commands, test commands,
or final verification. The main agent owns integration and evidence acceptance.

## Assignment Template

Each assignment must be self-contained:

```markdown
# Target
Exact files, symbols, base reference, current HEAD, and non-goals.

# Change Or Review Question
For review: dimensions to inspect and exact diff commands.
For investigation: symptom, search scope, and questions.
For design: current code, constraints, and rejected directions if artifact-backed.

# Acceptance
Expected output format, severity labels, and required evidence.
No project-wide build, test, or lint commands.
```

Implementation-oriented assignments must require current `HEAD` as the edit
base, diff-scoped formatting only, and no whole-file formatter churn unless the
task is explicitly formatting-only.

## Adversarial Review Prompt

Prepare the review brief through `cosim-gpu-review` or `cosim-gpu-rlcr-loop`.
The sub-agent prompt points to that brief and may include exact scoped diff
commands. It must not include the implementer's theory, desired result, or
dialogue summary.

For cosim-specific prompts, use
`references/cosim-gpu-review-prompts.md` when the touched subsystem matches
interrupt handling, PM4, cache or TLB behavior, HSA completion signals, or
similar GPU model paths.

## Evidence Recording

Save every consultation under the active task workspace:

```text
artifacts/<task-slug>/reviews/codex-<round>-<file>.md
artifacts/<task-slug>/reviews/codex-<round>-summary.md
```

Record prompt, file scope, response, classification, and disposition. If the
reviewer times out or errors, retry once with a narrower scope and record the
failure if it still fails.

## Provider Notes

Default to Codex for local code review and Gemini for research that needs
current external documentation. If the provider is unavailable and setup cost
exceeds review value, fall back to manual review and record that choice.

Proxy and sandbox issues are operational details, not review findings. When a
sub-agent creates stray files, verify with repository status and remove only
files proven to be sub-agent artifacts.
