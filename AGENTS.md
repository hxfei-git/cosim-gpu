# Repository Guidelines

## Project Structure & Module Organization

This repository orchestrates QEMU and gem5 for MI300X co-simulation. `gem5/` contains the simulator and GPU model, while `gem5-resources/` contains guest images, kernels, and workloads; both are Git submodules. Initialize them with `git submodule update --init --recursive`. Top-level orchestration lives in `scripts/`, SST configurations in `configs/sst/`, HIP tests in `tests/kernels/`, shared test helpers in `tests/common/`, and bilingual documentation in `docs/en/` and `docs/zh/`. Commit submodule changes in their own repositories before updating the top-level pointer.

## Build, Test, and Development Commands

- `./scripts/run_mi300x_fs.sh build-all` builds gem5, the guest disk, and test application. Use `build-gem5`, `build-disk`, or `status` for focused work.
- `docker build -t gem5-run:local -f scripts/Dockerfile.run scripts/` creates the runtime image.
- `./scripts/cosim_preflight.sh` performs a read-only prerequisite audit; `./scripts/cosim_launch.sh` starts co-simulation.
- `make -C tests` compiles HIP tests for `gfx942`; `bash tests/test_modprobe_params.sh` runs the fast driver-parameter regression.
- `./scripts/run_cosim_tests.sh vector_add` runs one operator in a fresh session; `--all` runs the full integration suite. These runs require Linux, KVM, Docker, QEMU with `vfio-user-pci`, and built guest assets.

## Coding Style & Naming Conventions

Use four-space indentation in Bash, Python, and HIP/C++. Quote shell expansions, use uppercase names for environment/configuration values, and lowercase `snake_case` for shell/Python functions and HIP test filenames. Python classes use `CamelCase`; C/C++ braces stay on the declaration line. The top level has no global formatter, so preserve nearby style. Run `shellcheck scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh`. Within `gem5/`, follow its pre-commit hooks and `MAINTAINERS.yaml` tags.

## Testing Guidelines

Add operator tests as `tests/kernels/<snake_case>.cpp`; the Makefile creates `tests/build/<stem>`. Reuse `tests/common/test_utils.h`, return nonzero on failure, and emit the standard `[PASS]` or `[FAIL]` summary. There is no numeric coverage threshold. Prefer fresh co-simulation sessions because guest state can affect later tests.

## Commit & Pull Request Guidelines

Follow the established Conventional Commit style, for example `fix(scripts): handle stale sockets` or `docs: update launch guide`. Keep commits topic-focused and sign them with `git commit -s`. PRs should target `main` and describe motivation, affected paths, linked issues, and exact validation commands/results. Call out submodule revisions and attach relevant logs for runtime failures. Keep English and Chinese documentation updates paired.

## Agent-Specific Instructions

Automation contributors must also follow `agents.md` and load the matching workflow under `.agents/skills/` before build, test, or debug work. Keep generated builds, logs, and `artifacts/` out of commits.
