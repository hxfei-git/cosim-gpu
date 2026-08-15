# Cosim GPU Repository Policy

This file contains project-specific repository maintenance rules. General Git
practice belongs in the main skill and should not be treated as unique to this
project.

## Repository Shape

The top-level repository records two nested repositories:

| Path | Meaning |
|---|---|
| `.agents` | reusable cosim GPU skills vendored in-tree |
| `gem5` | simulator source submodule |
| `gem5-resources` | resources and guest image inputs |

The superproject records `gem5` and `gem5-resources` commits as gitlinks.
`.agents` is ordinary top-level content with no separate skill repository or
gitlink. A dirty submodule work tree is not the same as a changed superproject
pointer.

## Skill Location

Reusable workflows live in `.agents/skills/` and are tracked directly by this
repository. Do not add new project-specific command implementations under
`.claude/commands`.

`AGENTS.md` and `CLAUDE.md` map to the top-level agent rules. Keep skill path
references current when skill folders are renamed.

## Commit Ownership

- Changes inside `.agents` are ordinary top-level repository changes and are
  committed directly in cosim-gpu.
- Changes inside `gem5` or `gem5-resources` must be committed in that submodule
  first. Then commit the top-level gitlink pointer.
- Top-level script, docs, tests, and ignore-rule changes belong to the
  superproject.
- Do not mix `gem5` or `gem5-resources` internal source changes and top-level
  pointer updates in one submodule commit; they are different repositories.

## Project Commit Rules

- gem5: pre-commit hooks apply; use tags from `MAINTAINERS.yaml` when relevant.
- Top-level cosim-gpu: no project-specific hooks.
- Sign commits with `Signed-off-by` from top-level git config unless the
  submodule has its own required author identity.

## Generated Outputs

Default exclude list for commits:

- `artifacts/`
- `m5out/`
- `local-cosim-runs/`
- `*.log`
- test `.out`, `.strace`, `.gdb`, `.proc`, and guest-run files
- local scratch scripts or one-off command transcripts unless explicitly
  promoted to repository scripts or docs

If a generated file is needed as durable evidence, prefer placing a concise
source document under `docs/` or an artifact summary under a task workspace.

## Documentation Pairing

Project docs under `docs/` must keep both `docs/zh/` and `docs/en/` versions.
The first line links to the other language version:

```text
[English](../en/<file>.md)
[中文](../zh/<file>.md)
```

When adding or modifying project docs, update both languages unless the file is
explicitly a temporary patch or source evidence artifact.

## Safe Submodule Pointer Review

Before committing a submodule pointer (`gem5` or `gem5-resources`; `.agents`
is vendored and has no pointer):

```bash
git submodule status
git -C <submodule> status --short
git -C <submodule> log --oneline -1
git diff --submodule=short -- <submodule>
```

If the submodule has local commits, confirm the top-level pointer records the
intended final commit. If the submodule is dirty, finish or separate that work
before committing the pointer.

## Splitting Skill Changes

For `.agents` skill work (now vendored in this repository), prefer commit
boundaries by reader-facing behavior:

- routing or trigger changes
- shared review or workflow contracts
- test, build, or debug reference extraction
- project policy or repository maintenance skills

After splitting, verify the final tree matches the intended working tree and
that references point to existing files.
