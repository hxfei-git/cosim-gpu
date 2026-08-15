# Program Identity And Guest Environment

Read this before launching a specified program, a ROCm example, a variant
kernel, or any row using guest environment prefixes.

## Program Identity

Before each specified program, prove the exact relative path and record it in
the active evidence file.

Local kernels:

```text
source: tests/kernels/<program>.cpp
binary: tests/build/<program>
runner argument: <program>
```

ROCm examples:

```text
source directory: external/rocm-examples/<relative-case-dir>
runner argument: <relative-case-dir>
```

Use exact checks:

```bash
test -f tests/kernels/<program>.cpp
test -x tests/build/<program> || make -C tests <program>
test -d external/rocm-examples/<relative-case-dir>
test -f external/rocm-examples/<relative-case-dir>/Makefile
```

When a task created a variant, test that exact variant. Do not substitute a
base program or nearby name. If the exact file or directory cannot be proven,
record `MISSING_PROGRAM` with the requested path and discovery output.

If a local filter can match multiple kernels, resolve the exact
`tests/kernels/<program>.cpp` first and pass only the unique basename.

For ROCm examples, separate build-entry failures from program failures. A
directory with `CMakeLists.txt` but no `Makefile`, dependency skip, or missing
guest binary is a runner or build adaptation issue until a real target binary
executes.

## Two-Phase Timeout

Probe first unless a benchmark entry exists:

```bash
bash scripts/run_cosim_tests.sh --test-timeout 30 vector_add
```

If the program completes, record wall-clock time. If it times out, double the
timeout and retry once.

Then use the estimator:

```bash
python3 tests/estimate_rocm_timeout.py --untested
python3 tests/estimate_timeout.py --test-bin tests/build/vector_add
```

Fallback defaults:

- local kernels: 60 seconds
- ROCm examples: 120 seconds
- known large grids: 300 seconds

## Device-Side Printf

Programs with device-side `printf` in kernel code hang in cosim. The estimator
flags these:

```bash
python3 tests/estimate_rocm_timeout.py
```

Skip them or mark them `CENSORED` with note
`device-side printf unsupported`.

## Guest Environment Prefix

For special environment rows:

```bash
GUEST_TEST_PREFIX="HSA_ENABLE_INTERRUPT=0" bash scripts/run_cosim_tests.sh vector_add
```

Guest workload defaults and prefix parsing are centralized in
`scripts/cosim_guest_env.sh`. Do not add ad hoc ROCm exports inside generated
guest commands; extend the shared helper instead.

The runner records effective `HSA_ENABLE_INTERRUPT` from guest output, not from
the host shell. If the guest log shows an environment assignment being treated
as an executable, treat that as a runner bug, fix the invocation path, and rerun
the intended program.

For environment policy questions, inspect `scripts/cosim_guest_env.sh`, then
`scripts/run_cosim_tests.sh`. Use debug only after a test row fails or a live
hang environment exists.
